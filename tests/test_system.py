import json
import socket
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import pytest
import torch
import uvicorn
from fastapi.testclient import TestClient
from torch import nn

from cads.gateway import create_app as gateway_app
from cads.inference import create_app as inference_app
from cads.policy import ARTIFACTS, perturb
from cads.protocol import EPSILONS, METHODS, ModelId, OpenRun, RunConfig
from cads.storage import RunStore
from cads.vehicle import Simulator


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    root = tmp_path_factory.mktemp("models")
    identity = ModelId(group="vanilla_dqn", seed=1)
    file = root / ARTIFACTS / identity.group / "model_1" / f"frame_{identity.frame}.pth"
    file.parent.mkdir(parents=True)
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(10)
        model = nn.Sequential(
            nn.Linear(50, 128), nn.ReLU(), nn.Linear(128, 128), nn.ReLU(), nn.Linear(128, 5)
        )
        torch.save(model.state_dict(), file)
    return root, model


@contextmanager
def replica(root: Path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(inference_app(root), log_level="error", ws="websockets-sansio"))
    thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 10
        while thread.is_alive() and not server.started and monotonic() < deadline:
            sleep(0.01)
        assert server.started
        yield f"ws://127.0.0.1:{port}/ws/infer"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


def open_run(client_socket, simulator, *, seed=0, steps=4, method="pgd", budget=0.01):
    config = RunConfig(
        seed=seed, model=ModelId(group="vanilla_dqn", seed=1), method=method, epsilon=budget, max_steps=steps
    )
    client_socket.send_text(OpenRun(config=config, lanes=simulator.lanes()).model_dump_json())
    return client_socket.receive_json()


def download(client, run_id):
    response = client.get(f"/runs/{run_id}/log")
    response.raise_for_status()
    return [json.loads(line) for line in response.text.splitlines()]


def play(client, seed=0, budget=0.01):
    with Simulator(seed) as simulator, client.websocket_connect("/ws/vehicle") as vehicle:
        run_id = open_run(vehicle, simulator, seed=seed, budget=budget)["run_id"]
        for seq in range(4):
            vehicle.send_text(simulator.step(seq).model_dump_json())
            action = vehicle.receive_json()
            assert action["type"] == "action", action
            outcome = simulator.apply(seq, action["action"])
            vehicle.send_text(outcome.model_dump_json())
            ack = vehicle.receive_json()
            assert ack["type"] == "committed", ack
            if ack["completed"]:
                break
    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    return run_id, download(client, run_id)


@pytest.mark.parametrize("method", METHODS)
def test_perturbation_is_bounded_and_repeatable(models, method):
    _, model = models
    before = [0.0, -1.0, 1.0, 0.2, -0.8] * 10
    for epsilon in EPSILONS:
        result, linf = perturb(before, method, epsilon, 3, 9, model)
        assert linf == max(abs(a - b) for a, b in zip(before, result, strict=True))
        assert linf <= epsilon + 1e-6
        assert result == perturb(before, method, epsilon, 3, 9, model)[0]
        assert (result == before) == (epsilon == 0)
    assert all(parameter.grad is None for parameter in model.parameters())


def test_real_clients_repeat_across_replica_counts_and_viewers(models, tmp_path):
    root, _ = models
    with replica(root) as first, replica(root) as second:
        with TestClient(gateway_app(root, tmp_path / "one.db", [first], workers=1)) as one:
            _, baseline = play(one)
        with TestClient(gateway_app(root, tmp_path / "two.db", [first, second], workers=1)) as two:
            with ThreadPoolExecutor(3) as threads:
                results = list(threads.map(lambda seed: play(two, seed), [0, 0, 12]))
            fields = ("seq", "observation", "perturbed_observation", "action", "scores", "reward", "crashed")
            for run_id, records in results[:2]:
                assert len(records) == len(baseline)
                assert [[row[key] for key in fields] for row in records] == [
                    [row[key] for key in fields] for row in baseline
                ]
                with two.websocket_connect(f"/ws/runs/{run_id}?after=1") as viewer:
                    assert viewer.receive_json()["type"] == "run"
                    for row in records[2:]:
                        assert viewer.receive_json() == {"type": "step", "record": row}
                    assert viewer.receive_json()["run"]["status"] == "completed"


def test_admission_malformed_input_and_failed_log(models, tmp_path):
    root, _ = models
    with TestClient(
        gateway_app(root, tmp_path / "runs.db", ["ws://127.0.0.1:1/ws/infer"], limit=1)
    ) as client:
        with Simulator(0) as simulator, client.websocket_connect("/ws/vehicle") as vehicle:
            run_id = open_run(vehicle, simulator)["run_id"]
            with client.websocket_connect("/ws/vehicle") as rejected:
                assert open_run(rejected, simulator)["code"] == "OVERLOADED"
            message = simulator.step(0).model_dump()
            message["observation"][0] = float("nan")
            vehicle.send_text(json.dumps(message))
            assert vehicle.receive_json()["code"] == "BAD_MESSAGE"
        assert client.get(f"/runs/{run_id}").json()["status"] == "failed"
        assert download(client, run_id) == []
        assert client.get("/load").json()["active_runs"] == 0


def test_live_viewers_receive_only_committed_steps(models, tmp_path):
    root, _ = models
    with (
        replica(root) as address,
        TestClient(gateway_app(root, tmp_path / "runs.db", [address])) as client,
        Simulator(0) as simulator,
        client.websocket_connect("/ws/vehicle") as vehicle,
    ):
        run_id = open_run(vehicle, simulator, budget=0, steps=1)["run_id"]
        path = f"/ws/runs/{run_id}"
        with client.websocket_connect(path) as first, client.websocket_connect(path) as second:
            assert (
                first.receive_json()["run"]["status"] == second.receive_json()["run"]["status"] == "running"
            )
            vehicle.send_text(simulator.step(0).model_dump_json())
            decision = vehicle.receive_json()
            assert decision["type"] == "action"
            assert download(client, run_id) == []
            vehicle.send_text(simulator.apply(0, decision["action"]).model_dump_json())
            assert vehicle.receive_json()["completed"]
            assert (
                first.receive_json()
                == second.receive_json()
                == {
                    "type": "step",
                    "record": download(client, run_id)[0],
                }
            )
            assert (
                first.receive_json()["run"]["status"] == second.receive_json()["run"]["status"] == "completed"
            )


def test_wrong_sequence_and_binary_receive_clean_errors(models, tmp_path):
    root, _ = models
    with (
        TestClient(gateway_app(root, tmp_path / "runs.db", ["ws://127.0.0.1:1/ws/infer"])) as client,
        Simulator(1) as simulator,
    ):
        with client.websocket_connect("/ws/vehicle") as binary:
            binary.send_bytes(b"{}")
            assert binary.receive_json()["code"] == "BAD_MESSAGE"
        with client.websocket_connect("/ws/vehicle") as vehicle:
            run_id = open_run(vehicle, simulator)["run_id"]
            vehicle.send_text(simulator.step(1).model_dump_json())
            assert vehicle.receive_json()["code"] == "STEP_ORDER"
        assert client.get(f"/runs/{run_id}").json()["status"] == "failed"


def test_replica_loss_fails_affected_run_and_healthy_replica_continues(models, tmp_path):
    root, _ = models
    with replica(root) as healthy:
        with replica(root) as first:
            with TestClient(gateway_app(root, tmp_path / "runs.db", [first, healthy])) as client:
                play(client, budget=0)
        with (
            TestClient(gateway_app(root, tmp_path / "runs.db", [first, healthy])) as client,
            Simulator(0) as simulator,
        ):
            with client.websocket_connect("/ws/vehicle") as vehicle:
                run_id = open_run(vehicle, simulator, budget=0)["run_id"]
                vehicle.send_text(simulator.step(0).model_dump_json())
                assert vehicle.receive_json()["code"] == "STEP_FAILED"
            assert client.get(f"/runs/{run_id}").json()["status"] == "failed"
            assert download(client, run_id) == []
            play(client, budget=0)


def test_restart_marks_unfinished_run_failed(tmp_path):
    path = tmp_path / "runs.db"
    first = RunStore(path)
    run_id = first.open_run({}, [])
    first.db.close()
    restarted = RunStore(path)
    assert restarted.run(run_id)["status"] == "failed"
    restarted.db.close()
