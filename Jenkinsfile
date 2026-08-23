pipeline {
    agent any

    environment {
        DOCKERHUB = credentials('dockerhub')
        IMAGE_SERVER = "kariimm557/app-cobaye-server"
        IMAGE_CLIENT = "kariimm557/app-cobaye-client"
    }

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
                sh 'docker build -t $IMAGE_SERVER:$BUILD_NUMBER -t $IMAGE_SERVER:latest ./server'
            }
        }

        stage('Build image client') {
            steps {
                echo 'Construction de l image du client...'
                sh 'docker build -t $IMAGE_CLIENT:$BUILD_NUMBER -t $IMAGE_CLIENT:latest ./client'
            }
        }

        stage('Login Docker Hub') {
            steps {
                echo 'Connexion à Docker Hub...'
                sh 'echo $DOCKERHUB_PSW | docker login -u $DOCKERHUB_USR --password-stdin'
            }
        }

        stage('Push images') {
            steps {
                echo 'Envoi des images vers Docker Hub...'
                sh 'docker push $IMAGE_SERVER:$BUILD_NUMBER'
                sh 'docker push $IMAGE_SERVER:latest'
                sh 'docker push $IMAGE_CLIENT:$BUILD_NUMBER'
                sh 'docker push $IMAGE_CLIENT:latest'
            }
        }
    }

    post {
        success {
            echo 'Pipeline terminé avec succès ! Images poussées sur Docker Hub.'
        }
        failure {
            echo 'Le pipeline a échoué. Vérifiez les logs.'
        }
        always {
            sh 'docker logout'
        }
    }
}
