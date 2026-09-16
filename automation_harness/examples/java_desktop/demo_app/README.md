# Java desktop demo application

This is a standalone Swing + JavaFX (`JFXPanel`) application for end-to-end
black-box qualification.  It exposes named controls through Java accessibility
metadata and includes a stable dark visual map with a green circular marker.

On Ubuntu with OpenJDK and OpenJFX installed:

```bash
mkdir -p build/classes
javac --module-path /usr/share/openjfx/lib --add-modules javafx.controls,javafx.swing \
  -d build/classes src/main/java/com/automationharness/demo/DesktopDemo.java
java --module-path /usr/share/openjfx/lib --add-modules javafx.controls,javafx.swing \
  -cp build/classes com.automationharness.demo.DesktopDemo
```

Current qualification scope is Swing and JavaFX desktop interaction. Browser
applications remain a supported future backend, but are not part of the current
visual or object-tree acceptance pass.

The harness must launch it with its Java ATK bridge enabled, rather than rely
on a test API inside the application.

For Ubuntu's Java ATK wrapper, add both
`-Xbootclasspath/a:/usr/share/java/java-atk-wrapper.jar` and
`-Djavax.accessibility.assistive_technologies=org.GNOME.Accessibility.AtkWrapper`
to the Java command. The removed managed `java-desktop` backend no longer
applies these settings. Launch Swing externally with these options, or perform
that launch in a contract-backed setup step before `live-desktop` actions run.

`DesktopDemo` offers semantic capture for its Swing button, checkbox, and the
`JavaFX visual region` host panel. Its JavaFX controls and canvas are real
`JFXPanel` content; Ubuntu's packaged OpenJFX 11 does not expose individual
JavaFX nodes through AT-SPI, so validate those by capturing the named host
region and using anchored visual checks or a masked baseline. `FxOnlyDemo` is
included as a standalone JavaFX target for native bridge tree and interaction qualification.

The intended condensed object-tree view is:

```text
Automation Harness Java Desktop Demo
├── Swing controls
│   ├── Follow Swing
│   └── Tracking enabled
└── JavaFX visual region

Automation Harness JavaFX Demo
├── JavaFX controls heading
├── Follow JavaFX
├── Demo progress
└── Demo visual map
    └── Demo map marker
```

Toolkit-only JPanel, VBox, BorderPane, skin, filler, and rendered-text wrappers
should not appear as separate authoring nodes. Named regions remain visible as
non-saveable structural context; controls and visual targets remain saveable
semantic objects or visual anchors.
