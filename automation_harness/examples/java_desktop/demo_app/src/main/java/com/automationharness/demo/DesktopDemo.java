package com.automationharness.demo;

import java.awt.BorderLayout;
import java.awt.Dimension;
import javax.swing.JButton;
import javax.swing.JCheckBox;
import javax.swing.JFrame;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.SwingUtilities;
import javafx.application.Platform;
import javafx.embed.swing.JFXPanel;
import javafx.geometry.Insets;
import javafx.scene.Scene;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressBar;
import javafx.scene.layout.Background;
import javafx.scene.layout.BackgroundFill;
import javafx.scene.layout.BorderPane;
import javafx.scene.layout.CornerRadii;
import javafx.scene.layout.Pane;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;
import javafx.scene.shape.Circle;

/** A deliberately small, accessibility-labelled black-box test target. */
public final class DesktopDemo {
    private DesktopDemo() { }

    public static void main(String[] args) {
        SwingUtilities.invokeLater(DesktopDemo::show);
    }

    private static void show() {
        JFrame frame = new JFrame("Automation Harness Java Desktop Demo");
        frame.setDefaultCloseOperation(JFrame.DISPOSE_ON_CLOSE);

        JPanel swing = new JPanel();
        JLabel label = new JLabel("Swing controls");
        label.getAccessibleContext().setAccessibleName("Swing controls heading");
        JButton follow = new JButton("Follow Swing");
        follow.getAccessibleContext().setAccessibleName("Follow Swing");
        follow.addActionListener(event -> follow.setText("Swing followed"));
        JCheckBox tracking = new JCheckBox("Tracking enabled", true);
        tracking.getAccessibleContext().setAccessibleName("Tracking enabled");
        swing.add(label);
        swing.add(follow);
        swing.add(tracking);

        JFXPanel fxPanel = new JFXPanel();
        fxPanel.setPreferredSize(new Dimension(640, 360));
        JPanel fxHost = new JPanel(new BorderLayout());
        fxHost.getAccessibleContext().setAccessibleName("JavaFX visual region");
        fxHost.add(fxPanel, BorderLayout.CENTER);
        Platform.runLater(() -> createScene(fxPanel));

        frame.add(swing, BorderLayout.NORTH);
        frame.add(fxHost, BorderLayout.CENTER);
        frame.pack();
        frame.setLocationByPlatform(true);
        frame.setVisible(true);
    }

    private static void createScene(JFXPanel panel) {
        VBox controls = new VBox(10);
        controls.setPadding(new Insets(14));
        Label heading = new Label("JavaFX controls and visual map");
        heading.setAccessibleText("JavaFX controls heading");
        Button follow = new Button("Follow JavaFX");
        follow.setAccessibleText("Follow JavaFX");
        follow.setOnAction(event -> follow.setText("JavaFX followed"));
        ProgressBar progress = new ProgressBar(0.72);
        progress.setAccessibleText("Demo progress");
        progress.setPrefWidth(250);

        Pane map = new Pane();
        map.setAccessibleText("Demo visual map");
        map.setPrefSize(590, 180);
        map.setBackground(new Background(new BackgroundFill(Color.web("#182336"), new CornerRadii(8), Insets.EMPTY)));
        Circle marker = new Circle(32, Color.web("#43d19e"));
        marker.setCenterX(300);
        marker.setCenterY(90);
        marker.setAccessibleText("Demo map marker");
        map.getChildren().add(marker);

        controls.getChildren().addAll(heading, follow, progress, map);
        BorderPane root = new BorderPane(controls);
        panel.setScene(new Scene(root, 640, 360));
    }
}
