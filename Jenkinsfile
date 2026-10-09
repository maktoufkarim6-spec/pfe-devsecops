// Pipeline CI de l'application cobaye.
// CI dans Jenkins (verifier, construire, scanner, publier) ; CD par Argo CD (GitOps).
// Jenkins n'a aucun acces au cluster : il ecrit seulement le nouveau tag d'image dans k8s/.
//
// Pre-requis Jenkins : credentials 'dockerhub' (user + token), 'github-token' (user + token
// GitHub avec droit d'ecriture sur ce depot), serveur SonarQube 'sonarqube', outil 'sonar-scanner'.

pipeline {
    agent any

    options {
        skipDefaultCheckout(true)
        disableConcurrentBuilds()
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    environment {
        DOCKERHUB    = credentials('dockerhub')
        IMAGE_SERVER = 'kariimm557/app-cobaye-server'
        IMAGE_CLIENT = 'kariimm557/app-cobaye-client'
        SCANNER_HOME = tool 'sonar-scanner'
        // SKIP, TAG et SONAR_STARTED_BY_PIPELINE sont definis par script (une variable du bloc
        // environment ne peut pas etre modifiee ensuite par un script).
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                script {
                    // Anti-boucle : le commit de deploiement fait par Jenkins ne relance pas la CI.
                    def last = sh(returnStdout: true, script: 'git log -1 --format=%s').trim()
                    if (last.contains('[skip ci]')) {
                        env.SKIP = 'true'
                        currentBuild.result = 'NOT_BUILT'
                        currentBuild.description = 'Commit de deploiement GitOps : CI ignoree'
                        echo 'Commit de deploiement GitOps detecte : pipeline ignore.'
                    } else {
                        env.TAG = "${env.BUILD_NUMBER}-" + sh(returnStdout: true, script: 'git rev-parse --short=7 HEAD').trim()
                        currentBuild.description = "Images ${env.TAG}"
                        echo "Tag des images : ${env.TAG}"
                    }
                }
            }
        }

        stage('Fenetre de maintenance AIOps') {
            when { expression { env.SKIP != 'true' } }
            steps {
                // Le build charge le serveur : on previent le moteur AIOps (expire seule apres 30 min).
                sh 'docker exec aiops-engine touch /data/maintenance || echo "aiops-engine absent : ignore"'
            }
        }

        stage('Tests serveur et couverture') {
            when { expression { env.SKIP != 'true' } }
            steps {
                // Les 27 tests unitaires de l'API tournent dans l'etape "build" du Dockerfile :
                // un test en echec arrete le pipeline ici. Le rapport de couverture est extrait pour SonarQube.
                sh '''
                    docker build --target build -t app-cobaye-server-tests:$TAG ./server
                    rm -rf server/coverage && mkdir -p server/coverage
                    cid=$(docker create app-cobaye-server-tests:$TAG)
                    docker cp "$cid:/app/coverage/lcov.info" server/coverage/lcov.info
                    docker rm "$cid" >/dev/null
                    docker rmi app-cobaye-server-tests:$TAG >/dev/null || true
                    # Chemins relatifs a la racine du depot, attendus par SonarQube
                    sed -i 's#^SF:src/#SF:server/src/#' server/coverage/lcov.info
                    echo "Fichiers couverts : $(grep -c '^SF:' server/coverage/lcov.info)"
                '''
            }
        }

        stage('Analyse SonarQube') {
            when { expression { env.SKIP != 'true' } }
            steps {
                script {
                    def running = sh(returnStdout: true, script: 'docker inspect -f "{{.State.Running}}" sonarqube 2>/dev/null || echo absent').trim()
                    if (running == 'false') {
                        sh 'docker start sonarqube'
                        env.SONAR_STARTED_BY_PIPELINE = 'true'
                    }
                }
                withSonarQubeEnv('sonarqube') {
                    // Attente que SonarQube soit operationnel (5 min max).
                    sh '''
                        for i in $(seq 1 60); do
                            if curl -sf "$SONAR_HOST_URL/api/system/status" | grep -q '"status":"UP"'; then
                                echo "SonarQube operationnel"; exit 0
                            fi
                            sleep 5
                        done
                        echo "SonarQube indisponible apres 5 min"; exit 1
                    '''
                    script {
                        // qualitygate.wait : le scanner attend le verdict et echoue si le Quality Gate echoue.
                        def rc = sh(returnStatus: true, script: '''
                            $SCANNER_HOME/bin/sonar-scanner \
                              -Dsonar.projectKey=app-cobaye \
                              -Dsonar.sources=. \
                              -Dsonar.exclusions=**/node_modules/**,**/build/**,**/coverage/**,**/*.png,**/*.ico,**/*.spec.ts,**/*.test.tsx \
                              -Dsonar.tests=server/src \
                              -Dsonar.test.inclusions=**/*.spec.ts \
                              -Dsonar.javascript.lcov.reportPaths=server/coverage/lcov.info \
                              -Dsonar.qualitygate.wait=true \
                              -Dsonar.qualitygate.timeout=300
                        ''')
                        if (rc != 0) {
                            // Diagnostic dans le journal Jenkins : quelles conditions, quels fichiers.
                            // set +x : le jeton SonarQube n'apparait jamais dans le journal.
                            sh '''
                                set +x
                                api() { curl -s -u "$SONAR_AUTH_TOKEN:" "$SONAR_HOST_URL/api/$1"; }
                                echo "===== Conditions du Quality Gate en echec ====="
                                api "qualitygates/project_status?projectKey=app-cobaye" | grep -o '{"status":"ERROR","metricKey"[^}]*}' || echo "(aucune)"
                                echo "===== Security Hotspots a revoir (code nouveau) ====="
                                api "hotspots/search?project=app-cobaye&status=TO_REVIEW&inNewCodePeriod=true&ps=100" \
                                  | grep -o '"component":"[^"]*"\\|"line":[0-9]*\\|"message":"[^"]*"' || echo "(aucun)"
                                echo "===== Problemes non resolus (code nouveau) ====="
                                api "issues/search?componentKeys=app-cobaye&inNewCodePeriod=true&resolved=false&ps=100" \
                                  | grep -o '"severity":"[^"]*"\\|"component":"[^"]*"\\|"line":[0-9]*\\|"message":"[^"]*"' || echo "(aucun)"
                            '''
                            error('Quality Gate SonarQube en echec : conditions detaillees ci-dessus.')
                        }
                    }
                }
            }
        }

        stage('Build image serveur') {
            when { expression { env.SKIP != 'true' } }
            steps {
                // Reutilise le cache de l'etape "Tests serveur" : les tests ne sont pas relances inutilement.
                sh 'docker build --pull -t $IMAGE_SERVER:$TAG -t $IMAGE_SERVER:latest ./server'
            }
        }

        stage('Build image client') {
            when { expression { env.SKIP != 'true' } }
            steps {
                sh 'docker build --pull -t $IMAGE_CLIENT:$TAG -t $IMAGE_CLIENT:latest ./client'
            }
        }

        stage('Scan securite Trivy') {
            when { expression { env.SKIP != 'true' } }
            steps {
                // Bloquant : toute faille CRITIQUE corrigeable, hors exceptions documentees dans .trivyignore.
                sh '''
                    for img in $IMAGE_SERVER:$TAG $IMAGE_CLIENT:$TAG; do
                        trivy image --scanners vuln --severity CRITICAL --ignore-unfixed \
                          --ignorefile .trivyignore --exit-code 1 --no-progress "$img"
                    done
                '''
                // Informatif : failles HIGH listees sans bloquer.
                sh '''
                    for img in $IMAGE_SERVER:$TAG $IMAGE_CLIENT:$TAG; do
                        trivy image --scanners vuln --severity HIGH --ignore-unfixed \
                          --exit-code 0 --no-progress "$img"
                    done
                '''
            }
        }

        stage('Login Docker Hub') {
            when { expression { env.SKIP != 'true' } }
            steps {
                sh 'echo $DOCKERHUB_PSW | docker login -u $DOCKERHUB_USR --password-stdin'
            }
        }

        stage('Push images') {
            when { expression { env.SKIP != 'true' } }
            steps {
                sh '''
                    docker push $IMAGE_SERVER:$TAG
                    docker push $IMAGE_SERVER:latest
                    docker push $IMAGE_CLIENT:$TAG
                    docker push $IMAGE_CLIENT:latest
                '''
            }
        }

        stage('Mise a jour GitOps') {
            when { expression { env.SKIP != 'true' } }
            steps {
                // Ecrit le nouveau tag dans k8s/ : Argo CD detecte le commit et deploie.
                withCredentials([usernamePassword(credentialsId: 'github-token',
                                                  usernameVariable: 'GIT_USER',
                                                  passwordVariable: 'GIT_TOKEN')]) {
                    sh '''
                        git config user.name  "jenkins-ci"
                        git config user.email "jenkins-ci@pfe.local"
                        git config credential.helper '!f() { echo "username=${GIT_USER}"; echo "password=${GIT_TOKEN}"; }; f'
                        git fetch origin main
                        git checkout -B gitops-update origin/main
                        sed -i -E "s#(image: ${IMAGE_SERVER}):[^[:space:]]+#\\1:${TAG}#" k8s/server.yaml
                        sed -i -E "s#(image: ${IMAGE_CLIENT}):[^[:space:]]+#\\1:${TAG}#" k8s/client.yaml
                        grep -n "image:" k8s/server.yaml k8s/client.yaml
                        if git diff --quiet; then echo "Manifestes deja a jour"; exit 0; fi
                        git commit -am "[skip ci] Deploiement des images ${TAG}"
                        ok=0
                        for i in 1 2 3; do
                            if git push origin HEAD:main; then ok=1; break; fi
                            git pull --rebase origin main
                        done
                        git config --unset credential.helper
                        [ "$ok" = 1 ]
                    '''
                }
            }
        }
    }

    post {
        success {
            echo "Pipeline termine : code analyse, images ${env.TAG} scannees, publiees et deployees par Argo CD."
        }
        failure {
            echo 'Le pipeline a echoue : aucune image n a ete deployee. Verifiez l etape en rouge.'
        }
        always {
            sh 'docker logout || true'
            sh 'git config --unset credential.helper || true'
            script {
                if (env.SONAR_STARTED_BY_PIPELINE == 'true') {
                    // SonarQube consomme ~2 Go : on le rearrete s'il etait arrete avant le build.
                    sh 'docker stop sonarqube || true'
                }
            }
        }
    }
}
