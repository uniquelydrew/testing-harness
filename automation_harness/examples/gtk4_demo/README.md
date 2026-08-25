# GTK 4.14 Demo baseline

Each directory is an isolated GTK 4.14.x bundle. Run all of them with
`automation-run gtk-demo selftest`, or run one with
`automation-run run <bundle> --backend gtk-demo`.

The repositories are semantic AT-SPI baselines. Recapture every entry with
`automation-capture` after an intentional GTK minor-version upgrade; do not
reuse ordinal or geometry locators.
