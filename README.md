# P7-Internet-Connected-Autonomous-Driving-System

Specification: [`docs/SPEC.md`](docs/SPEC.md) (requirements, architecture options and recommendation, verification plan, schedule, risks).

## Open questions

Decided: schedule and roles, robustness to perturbations as the only "security" scope, simulation only on `highway-v0`, reuse of the pretrained agents from the models repository, the four attacks and the epsilon grid, local-only deployment, no communication-fault emulation. Spec v0.5 proposes a new topology (vehicle clients, gateway, stateless inference tier) and new transports (gRPC, SSE); these are not yet agreed (Q5, Q7). Still open:

| ID | Question | Why it matters |
|----|----------|----------------|
| Q1 | Which feature groups of the 50-dimensional observation may the attacker perturb (all, as in the models repository, or e.g. not `presence`)? | Threat model; keeps results comparable with the models repository |
| Q2 | Licence and attribution of the models repository and the vendored WocaR-RL and `auto_LiRPA` code. May the needed files be copied into this repository, or should the models repository be a dependency (submodule or package)? | Repository structure and the report |
| Q3 | What concurrency numbers does the supervisor expect for the local load test (simultaneous sessions, viewers per session)? Placeholders: 50 sessions, 5 viewers each | Acceptance criteria of NFR-SCAL-1 |
| Q4 | Is a 23 to 31 percent clean crash rate acceptable as agent quality for demos and experiments, or is improving clean driving (new training) a goal? Which single seed is shown in demos? | Baseline for demos and NFR-SAF-3 |
| Q5 | Does the supervisor accept that "unreliable communication" is delimited out of scope, and that the vehicles are the clients of the service (D1-c)? | The two structural changes of v0.5 |
| Q6 | Does the UI need anything beyond a single local page (accounts, run history per user)? Default: no | Scope |
| Q7 | Transport: gRPC and SSE as recommended, WebSocket as in v0.4, or a broker (NATS)? To be frozen at M0 | D5, R6 |
| Q8 | What does the course expect of the UI evaluation (method, number and kind of participants)? | FR-23, NFR-UX-1 |
