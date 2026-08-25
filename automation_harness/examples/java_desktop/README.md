# Java desktop bundle template

Copy this directory outside the installed package, rename `manifest.template.yaml` to `manifest.yaml`, and replace the command, expected application name, component identities, baseline, and mask with values captured from the real application.

The bundle deliberately contains no application fixture and no test hook. It launches a known Java command, resolves controls through platform accessibility, and validates visual output from the framebuffer.
