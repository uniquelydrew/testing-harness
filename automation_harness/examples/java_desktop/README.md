# Java desktop bundle template

Copy this directory outside the installed package, rename
`manifest.template.yaml` to `manifest.yaml`, and replace the component
identities, baseline, and mask with values captured from the real application.
Launch the application before running this legacy Python bundle.

The bundle deliberately contains no application fixture and no test hook. It
attaches to the current desktop, resolves controls through platform
accessibility, and validates visual output from the framebuffer. New workflows
should prefer a self-contained `.ahplan`; application startup can be an
ordinary contract-backed script step.
