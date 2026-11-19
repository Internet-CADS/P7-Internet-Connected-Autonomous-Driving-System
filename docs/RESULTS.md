# System verification

19 November 2026 · Windows 11, Intel Core i5-12600KF, 16 logical CPUs

## End-to-end runs

- Docker Compose started the gateway and inference service, and the bundled WocaR-DQN/PGD demo completed with seven committed steps. The complete JSONL trace was available through the gateway and each logged perturbation met its bound.
- The 15-case smoke grid completed with one replica and again with two. All 114 steps matched exactly across the two runs for clean and perturbed observations, both actions and Q-vectors, vehicle poses, reward and terminal flags. The grid covers all three checkpoint groups, a clean baseline and all four perturbation methods at ε = 0.01. It caps each episode at eight steps.
- Ten automated tests passed. They cover bounded deterministic perturbations, concurrent and repeatable runs, admission, malformed messages, live viewers, replay, replica loss and recovery of interrupted run records. An Edge browser check exercised timeline navigation, the five action scores and the 50-row comparison table.

## Local load

Each unpaced synthetic comparison offered 20 lockstep steps to 50 simultaneous runs with five live viewers per run. Each configuration completed 1,000 steps and delivered 5,000 viewer events. Throughput includes connection setup. RTT is measured by the vehicle client and viewer delay is measured from the recorded commit timestamp to message receipt. The Docker image build and other verification work overlapped these samples. The timings describe the observed runs and are insufficient to establish a replica scaling trend.

| Replicas | Steps/s | Vehicle RTT p50 / p95 / p99 (ms) | Viewer delay p50 / p95 / p99 (ms) |
|---:|---:|---:|---:|
| 1 | 42.8 | 789.0 / 3,752.5 / 4,967.6 | 26.4 / 264.9 / 473.7 |
| 2 | 31.5 | 856.8 / 4,309.5 / 10,247.3 | 108.3 / 477.2 / 2,836.8 |
| 4 | 76.1 | 114.0 / 1,187.9 / 1,934.0 | 49.3 / 384.9 / 758.7 |

In an overload trial, the gateway admitted 50 of 100 simultaneous attempts. All 50 admitted runs completed and the other 50 received `OVERLOADED`. A separate trial used ten actual simulator instances and two viewers per run. All 80 offered steps completed, delivering 160 viewer events.

The packaged stack also completed 400 steps with 50 synthetic clients offering one step per second each and 250 viewers. All 2,000 viewer messages arrived. The sends started together. Vehicle RTT was 985 / 1,440 / 1,497 ms at p50 / p95 / p99, and viewer delay was 50.7 / 118.6 / 144.7 ms. A post-run container snapshot showed 342 MiB for the gateway and 169 MiB for inference. The paced trial confirms capacity for that test to finish, with high decision latency during the synchronized offers.

## Remaining evaluation

The [reduced grid](../experiments/reduced.json) and [monitoring study](USABILITY.md) are ready to execute. The 15-case smoke comparison verifies the service path. A robustness ranking across checkpoint seeds requires the longer grid. The UI study still requires participants.
