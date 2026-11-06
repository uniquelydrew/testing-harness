pipeline {
  agent none

  options {
    timestamps()
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '20', artifactNumToKeepStr: '10'))
  }

  parameters {
    booleanParam(
      name: 'RUN_DESKTOP_QUALIFICATION',
      defaultValue: false,
      description: 'Run the RHEL 8 desktop lane on a qualified interactive runner.'
    )
  }

  stages {
    stage('Python unit') {
      agent { label 'linux && python312' }
      steps {
        checkout scm
        sh 'bash scripts/ci/jenkins.sh unit'
      }
      post {
        always {
          junit allowEmptyResults: true, testResults: 'artifacts/jenkins/unit/pytest.xml'
          archiveArtifacts allowEmptyArchive: true, artifacts: 'artifacts/jenkins/unit/**'
        }
      }
    }

    stage('Instrumentation agents') {
      agent { label 'linux && jdk11' }
      steps {
        checkout scm
        sh 'bash scripts/ci/jenkins.sh agents'
      }
      post {
        success {
          archiveArtifacts artifacts: 'java-agent/build/*.jar, javafx_agent/build/*.jar', fingerprint: true
        }
      }
    }

    stage('RHEL desktop qualification') {
      when {
        beforeAgent true
        expression { return params.RUN_DESKTOP_QUALIFICATION }
      }
      agent { label 'linux && rhel8 && desktop' }
      steps {
        checkout scm
        sh 'bash scripts/ci/jenkins.sh desktop'
      }
      post {
        always {
          junit allowEmptyResults: true, testResults: 'artifacts/jenkins/desktop/*.xml'
          archiveArtifacts allowEmptyArchive: true, artifacts: 'artifacts/jenkins/desktop/**'
        }
      }
    }
  }
}
