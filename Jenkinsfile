pipeline {
    agent any

    stages {
        stage('Checkout') {
            steps {
                echo 'Récupération du code depuis Git...'
                checkout scm
            }
        }

        stage('Build image serveur') {
            steps {
                echo 'Construction de l image du serveur...'
                sh 'docker build -t app-cobaye-server:${BUILD_NUMBER} ./server'
            }
        }

        stage('Build image client') {
            steps {
                echo 'Construction de l image du client...'
                sh 'docker build -t app-cobaye-client:${BUILD_NUMBER} ./client'
            }
        }
    }

    post {
        success {
            echo 'Pipeline terminé avec succès ! Les images sont construites.'
        }
        failure {
            echo 'Le pipeline a échoué. Vérifiez les logs.'
        }
    }
}
