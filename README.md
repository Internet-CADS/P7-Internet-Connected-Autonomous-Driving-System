# P7-Internet-Connected-Autonomous-Driving-System

Specification: [`docs/SPEC.md`](docs/SPEC.md) (requirements, architecture options and recommendation, verification plan, schedule, risks).

## Open questions

Everything else in the spec is decided (schedule and roles, robustness to perturbations as the only "security" scope, simulation only on `highway-v0`, reuse of the pretrained agents from the models repository, the four attacks and the epsilon grid, local-only deployment). These are still open:

| ID | Question | Why it matters |
|----|----------|----------------|
| Q1 | Which feature groups of the 50-dimensional observation may the attacker perturb (all, as in the models repository, or e.g. not `presence`)? | Threat model; keeps results comparable with the models repository |
| Q2 | Licence and attribution of the models repository and the vendored WocaR-RL and `auto_LiRPA` code. May the needed files be copied into this repository, or should the models repository be a dependency (submodule or package)? | Repository structure and the report |
| Q3 | What concurrency numbers does the supervisor expect for the local load test (simultaneous sessions, viewers per session)? Placeholders: 50 sessions, 5 viewers each | Acceptance criteria of NFR-SCAL-1 |
| Q4 | Is a 23 to 31 percent clean crash rate acceptable as agent quality for demos and experiments, or is improving clean driving (new training) a goal? Which single seed is shown in demos? | Baseline for demos and NFR-SAF-3 |
| Q5 | Are communication faults evaluated in real-time mode (non-deterministic) or in lockstep with emulated, seeded delay? | Experiment design (D6) |
| Q6 | Does the UI need anything beyond a single local page (accounts, run history per user)? Default: no | Scope |
