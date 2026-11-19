# Internet Connected Autonomous Driving System

Status: v0.7, 19 November 2026. This revision uses the attached v0.6 as the team's scope and records the decisions for its open questions. The [proposal](proposal.pdf) defines B1–B4. The [decision record](DECISIONS.md) identifies assumptions and external confirmations. The [plan](PLAN.md) specifies interfaces, acceptance tests and milestones.

## 1. Objectives

- B1: Provide a service through which independent driving environments exchange observations and actions with a DRL agent.
- B2: Formulate the simulated driving task in RL terms, serve the pretrained DQN variants, and evaluate the effects of bounded state perturbations and the pretrained defences.
- B3: Provide a browser monitoring page for vehicle states, agent actions and scores, perturbations and agent responses.
- B4: Evaluate robustness, service scalability and quality of service, and the monitoring interface.

## 2. Agreed scope from v0.6

- Use only the rev3 `highway-v0` configuration, on one local CPU-only machine. Run independent seeded simulator instances.
- Serve the existing vanilla DQN, SA-DQN and WocaR-DQN checkpoints. The plan uses the verified rev3 peak-of-mean frames with seven checkpoint seeds per group.
- Support random L∞ noise, 10-step PGD, MinBest and worst-Q at ε ∈ {0, 0.0025, 0.005, 0.0075, 0.01}.
- Use lockstep state perturbation experiments. A step failure marks the run failed and excludes it from experiment analysis.
- Simulation only. Communication-fault emulation, fallback actions, a real-time control mode, network security, new training and deployment beyond one machine are out of scope.
- Plan for five weeks with about three build weeks and three principal implementers on a six-person team.

## 3. Principles

- Given the same configuration and seed, observations, perturbations and selected actions are reproducible across concurrent runs and replica counts.
- Each perturbation is a pure function of the observation, model, method, ε, environment seed and step index. Policy weights remain fixed.
- Every committed step stores the original observation and the observation served to the policy.

## 4. Architecture

The vehicle client owns simulation and starts the run. A single gateway validates input, tracks sessions, computes bounded perturbations in a CPU process pool, calls inference, commits run outcomes and provides browser feeds. Stateless inference replicas load the pinned models with plain PyTorch and return a discrete greedy action and five Q-values. Vehicle and inference links exchange versioned JSON messages over WebSocket. The browser uses REST for history and export and WebSocket for live events and replay. Use SQLite WAL for run records and Compose for one-command startup, with a plain-process development mode. See A1–A4 and T1–T4 in the [decision record](DECISIONS.md).

## 5. Evaluation

- Robustness: compare all three model groups across paired environment seeds, with at least three checkpoint seeds per group, all four methods, the ε grid, and 100 episodes per condition as budget allows. Compare WocaR with vanilla under the gradient perturbations at ε = 0.01. Report crash rates, returns, episode lengths, action differences and between-seed spread.
- QoS and scale: measure service throughput and latency percentiles for multiple replica counts, plus admission overload and an unexpected replica exit. Start with up to 50 runs and five viewers per run as a measurement point.
- Monitoring: task-based evaluation with about five participants, measuring completion and time. Include SUS if the course confirms that requirement.

## 6. Scope decisions and confirmations

Use the full 50-feature observation and the reference's ε-ball projection, permitting the same small excursions past nominal feature limits. Use assets from the pinned models checkout and confirm redistribution permissions with the rights holders. Show the existing agent baseline in a local read-only browser interface with live view and replay. The existing clean and perturbed crash results are comparative simulation measurements.

The supervisor's confirmation is pending on the project scope around unreliable communication and whether the prior method adaptation plus the project's perturbation and defence analysis satisfy B2. Course confirmation for the UI evaluation and the actual team assignments are also pending. See Q1–Q8 and T6 in the [decision record](DECISIONS.md).
