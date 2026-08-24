# Planned implementation surface

These names originated in the preserved architecture skeleton but are intentionally **not** shipped as empty importable modules. They are added only when an implementation vertical slice exists.

## Core

- `core/dds_context.py`
- `core/environment.py`
- `core/tracking_state.py`

## Protected/service adapters

- `drivers/hooks/camera_controller_driver.py`
- `drivers/hooks/cue_service_driver.py`
- `drivers/hooks/dds_driver.py`
- `drivers/hooks/estalt_driver.py`
- `drivers/hooks/flightplan_driver.py`
- `drivers/hooks/modes_driver.py`
- `drivers/hooks/msct_hook_driver.py`
- `drivers/hooks/session_manager_driver.py`
- `drivers/hooks/tcc_driver.py`

These remain protected-environment work and are not to be fabricated in the public/reference harness.

## Utilities and domain steps

- `steps/msct_steps.py`
- `utils/dds_utils.py`
- `utils/geometry.py`
- `utils/video_utils.py`

`geometry.py` and `video_utils.py` are expected to return when the image-space tracking/typed-coordinate vertical slice is implemented.

## Environment profiles

- `resources/environments/camera_site_a.yaml`
- `resources/environments/dev_fc.yaml`
- `resources/environments/fc1.yaml`
- `resources/environments/fc2.yaml`
- `resources/environments/low_side_vm.yaml`

No empty environment profiles are distributed because an empty profile can falsely imply that an environment contract exists.

## Legacy empty test filenames

The original zero-byte test placeholders were removed. Executable synthetic equivalents live under `automation_harness/examples/reference_suite/` and `automation_harness/examples/reference_ui/`.
