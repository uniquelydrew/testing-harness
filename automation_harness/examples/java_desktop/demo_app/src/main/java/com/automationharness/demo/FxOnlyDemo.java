package com.automationharness.demo;

import javafx.application.Application;
import javafx.geometry.Insets;
import javafx.scene.Scene;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressBar;
import javafx.scene.layout.Background;
import javafx.scene.layout.BackgroundFill;
import javafx.scene.layout.CornerRadii;
import javafx.scene.layout.Pane;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;
import javafx.scene.shape.Circle;
import javafx.stage.Stage;

/** Standalone JavaFX accessibility qualification target. */
public final class FxOnlyDemo extends Application {
    @Override
    public void start(Stage stage) {
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
        VBox root = new VBox(10, heading, follow, progress, map);
        root.setPadding(new Insets(14));
        stage.setTitle("Automation Harness JavaFX Demo");
        stage.setScene(new Scene(root, 640, 360));
        stage.show();
    }
}
