# Checkpoints: source

The 21 policy checkpoints in this folder are copied, unchanged, from the models repository.

- Repository: https://github.com/Internet-CADS/models
- Commit: `64a52648080ad5d4aa52bfa48ebdcf89356a5241` (the checkpoints were last changed in `a0958fe4df687dd757cdc45f226c16360471dd28`)
- Original folder: `entries/05_wocar_dqn_r3/artifacts/run_2/<group>/model_<seed>/frame_<frame>.pth`
- Frames: each group at its peak-of-mean frame (the frame where the seed-mean mini-test return is highest), as chosen by the repository's `evaluate.py` / `report.py`:
  - `vanilla_dqn`: 1,500,000
  - `sa_dqn`: 1,600,000
  - `wocar_dqn`: 1,450,000
- Each file is the state dict of the repository's `QNetwork.features` (`nn.Sequential`: Linear 50→128, ReLU, Linear 128→128, ReLU, Linear 128→5).

| File here | Original path | SHA-256 |
|---|---|---|
| `sa_dqn/model_1_frame_1600000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/sa_dqn/model_1/frame_1600000.pth` | `c1a42f93aafdc667…` |
| `sa_dqn/model_2_frame_1600000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/sa_dqn/model_2/frame_1600000.pth` | `143f5a3f030e39d1…` |
| `sa_dqn/model_3_frame_1600000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/sa_dqn/model_3/frame_1600000.pth` | `d1965318f3cf1fe8…` |
| `sa_dqn/model_4_frame_1600000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/sa_dqn/model_4/frame_1600000.pth` | `48645ad336bdef8a…` |
| `sa_dqn/model_5_frame_1600000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/sa_dqn/model_5/frame_1600000.pth` | `4b21907af42043b7…` |
| `sa_dqn/model_6_frame_1600000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/sa_dqn/model_6/frame_1600000.pth` | `b7bcbad510a093b5…` |
| `sa_dqn/model_7_frame_1600000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/sa_dqn/model_7/frame_1600000.pth` | `95673bc9bfc29e1b…` |
| `vanilla_dqn/model_1_frame_1500000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/vanilla_dqn/model_1/frame_1500000.pth` | `58c2f08f5c26df3c…` |
| `vanilla_dqn/model_2_frame_1500000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/vanilla_dqn/model_2/frame_1500000.pth` | `b22e5e95b9371e3f…` |
| `vanilla_dqn/model_3_frame_1500000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/vanilla_dqn/model_3/frame_1500000.pth` | `12e96b2c85872731…` |
| `vanilla_dqn/model_4_frame_1500000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/vanilla_dqn/model_4/frame_1500000.pth` | `5187fd7f4601c113…` |
| `vanilla_dqn/model_5_frame_1500000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/vanilla_dqn/model_5/frame_1500000.pth` | `759c8f000c03a152…` |
| `vanilla_dqn/model_6_frame_1500000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/vanilla_dqn/model_6/frame_1500000.pth` | `77e0e9e50d52e6fe…` |
| `vanilla_dqn/model_7_frame_1500000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/vanilla_dqn/model_7/frame_1500000.pth` | `5e1679c7d971a11e…` |
| `wocar_dqn/model_1_frame_1450000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/wocar_dqn/model_1/frame_1450000.pth` | `a459953c66db02ed…` |
| `wocar_dqn/model_2_frame_1450000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/wocar_dqn/model_2/frame_1450000.pth` | `47621fb32719ddc6…` |
| `wocar_dqn/model_3_frame_1450000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/wocar_dqn/model_3/frame_1450000.pth` | `dcfff64af60404dd…` |
| `wocar_dqn/model_4_frame_1450000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/wocar_dqn/model_4/frame_1450000.pth` | `86a780cc9bc2aab9…` |
| `wocar_dqn/model_5_frame_1450000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/wocar_dqn/model_5/frame_1450000.pth` | `16e025df0fd6874f…` |
| `wocar_dqn/model_6_frame_1450000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/wocar_dqn/model_6/frame_1450000.pth` | `dc1f9244809fcb2b…` |
| `wocar_dqn/model_7_frame_1450000.pth` | `entries/05_wocar_dqn_r3/artifacts/run_2/wocar_dqn/model_7/frame_1450000.pth` | `78f08256d1538384…` |
