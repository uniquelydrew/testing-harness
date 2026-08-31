# Object-Action Authoring Workflow

This branch replaces the misleading global Step Library authoring surface with
an object-scoped workflow.

## Delivered

- Authoring project manifests connect repository, run artifacts, environment
  setup, and target configuration.
- The GUI can create/open projects, configure targets, launch and stop a target,
  and reuse the same managed target for capture and test execution.
- Open applications are first-class `attached-desktop` targets. The first
  capture binds the project to its live application identity; attached runs do
  not launch or terminate the user's process.
- New projects no longer default to the synthetic reference application.
- Selecting a captured object populates an Actions view from its semantic type
  and supported repository actions.
- Unsupported or unimplemented semantic operations are not offered.
- Adding an action automatically binds the selected object and opens typed input
  fields when configuration is required.
- Test Composer is renamed Test Flow; Run Reference is renamed Run Test and
  respects project target configuration.
- Wait, assertion, and property observation operations are offered alongside
  object interactions.
- Reusable steps are created explicitly from user-authored test compositions.
  No initial global step library is presented.
- The internal registered-step catalog remains the execution adapter layer and
  CLI diagnostic surface for serialization compatibility.
- Bare `automation-run` now returns help instead of raising `AttributeError` on
  the Python 3.6 compatibility path.
- `automation-reference` has a safe default Unix socket for direct development
  launches; managed execution may still supply a unique socket.

## Project example

See `automation_harness/examples/authoring_project/project.yaml`.
