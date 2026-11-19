# Decision record

19 November 2026. Rows marked `[ASSUMED]` record implementation choices where the team did not specify a preference. All other rows record the team's decisions. Supervisor and course confirmations are tracked with the corresponding decision.

| ID | Decision | Alternative | Reason |
|---|---|---|---|
| A1 | Separate vehicle clients, one gateway and stateless inference replicas. | One simulation service. | Real clients exercise B1 and leave the simulator CPU outside the gateway. |
| A2 | WebSocket/JSON for the vehicle, inference and live browser links. REST for run history and export. | gRPC and SSE, or a broker. | Team transport choice. The gateway routes each inference request over reusable connections. |
| A3 | Gateway process pool runs bounded perturbations and uses the same model identity as inference. | Separate perturbation service. | Isolate CPU work from the event loop while keeping local packaging simple. |
| A4 | The vehicle client starts runs. | Browser starts runs. | The vehicle owns the environment and its lifecycle. |
| Q1 | Perturb all 50 values in the ε-ball around the clean observation. Allow the small range excursions observed in the evaluator. | Enforce feature bounds or preserve presence. | Direct comparison with the verified reference function. Validate the clean range and the perturbed distance separately. |
| Q2 | [ASSUMED] Use a pinned, authorised models checkout or submodule at `64a52648080ad5d4aa52bfa48ebdcf89356a5241`. Use a plain PyTorch loader. | Vendor source and assets with permission. | Checkpoint equivalence was measured. The team-specific code and weights have unresolved redistribution terms. Confirm rights with the owners and supervisor. |
| Q3 | Probe 50 runs and five viewers per run. Set thresholds after measurement. | Set a fixed target now. | Both gateway capacity and machine contention have to be measured. |
| Q4 | Use the existing clean baseline in the demo and report relative robustness. | Require a stronger clean baseline. | Rev3 reported 23–31% clean crash rates, and model changes would need separate evaluation. |
| Q5 | Use the attached spec's bounded state perturbations and lockstep across independent highway clients. Supervisor confirmation pending. | Include communication faults. | Proposal A names unreliable communication. The attached spec centers the experiment on state perturbations. |
| Q6 | One local read-only monitoring page with run history, live view and replay. | Multiple pages or accounts. | The requested B3 observations fit a single monitoring view. |
| Q7 | About five participants, task success and time, with SUS if confirmed. Course requirement pending. | Scripted walkthrough or another course method. | Tasks assess whether people can find and interpret the monitoring data. |
| Q8 | Document the RL formulation, existing method adaptation, and defence and perturbation analysis. Supervisor confirmation of B2 coverage pending. | Additional method development if required by the supervisor. | B2 explicitly calls for method design. The approved scope will determine whether the team's existing method work suffices. |
| T1 | Python 3.13, uv lockfile, Ruff, pytest and GitHub Actions. [ASSUMED] Share versioned Pydantic message definitions for the selected JSON transport. | pip tooling or hand-maintained JSON schemas. | The CPU runtime and reference inference were verified on 3.13. |
| T2 | Derive a per-step NumPy generator from the environment seed and sequence. Failed steps end the run. Test under one and several replicas. | Mutable RNG streams and in-run retries. | Request order cannot change a run's perturbation, and retries require duplicate-outcome semantics. |
| T3 | SQLite WAL, JSONL for run download, Parquet for experiment results, plain JavaScript and canvas for monitoring. | JSONL as the primary store or a visualisation framework. | Durable replay and crash recovery with a small local deployment footprint. |
| T4 | One gateway, configurable admission and round-robin inference over healthy replicas. Compose plus local processes. | Multiprocess gateways or local processes alone. | One owner simplifies session and logging consistency, and separate inference workers support scaling experiments. |
| T5 | Pair environment seeds across conditions, with at least three checkpoint seeds per group, the four methods, all five budgets and 100 episodes per cell as budget allows. | Replicate the full rev3 sweep. | The smaller grid targets B4 and reports between-seed spread within the local compute budget. |
| T6 | Split implementation into protocol/vehicle, gateway/inference and UI/packaging. Assign experiment, verification and integration roles across the rest of the team. Team names and experience pending. | Assign based on current skills once known. | Gives each interface an owner and keeps work parallel. |

## Archive conflicts

- The attached v0.6 supersedes v0.4's real-time mode, deadlines as driving semantics, communication-fault emulation, fallback actions, recovery of interrupted runs, communication experiment, and optional network authentication. Old FR-06, FR-07, NFR-SAF-1/2, NFR-LAT-2 and NFR-REL-1 are retired. Legacy malformed-input, scaling and performance criteria are replaced by lockstep validation and local measurements.
- The attached v0.6 specifies `highway-v0` only, one local machine and pretrained models. Extra environments, deployment and new training from the old options are outside the agreed technical scope.
- The reference checkpoint architecture is a plain 50→128→128→5 MLP. The old dueling-head description and inference dependency on `auto_LiRPA` were corrected in the feasibility check.
- The old range restriction conflicts with the reference's full-vector ε-ball. Q1 records the choice to match the reference. A1 uses independent vehicle clients. A2 adopts WebSocket/JSON and REST for the revised topology.
