// Declarative Jenkins pipeline for the Stump AI Django service.
//
// Prerequisites on the Jenkins controller
//   1. Docker + the docker-compose plugin (or the CLI on PATH).
//   2. A "docker-agent" node, or the built-in node with the socket mounted.
//   3. Credentials:
//        - registry-credentials  : username + token for the container registry
//        - stump-ai-deploy       : SSH key for the production host (optional)
//      Use Manage Jenkins > Credentials to add both, then reference their IDs
//      below. If you deploy with `docker compose pull` against Docker Hub,
//      the registry credential is what lets the target host pull.
//
// Pipeline flow
//   checkout -> static checks (lint/test) -> build image -> smoke test ->
//   publish image -> deploy (compose up) -> post actions
//
// How to run it
//   1. New Item > Pipeline > Pipeline script from SCM, point SCM at your repo.
//   2. Ensure this file is named Jenkinsfile at the repo root.
//   3. Build Now.

pipeline {
    agent any

    options {
        timestamps()
        ansiColor('xterm')
        timeout(time: 45, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '10'))
        disableConcurrentBuilds()
    }

    environment {
        IMAGE_NAME        = 'stump-ai'
        IMAGE_TAG         = ''
        IMAGE_FULL        = ''
        COMPOSE_PROJECT   = 'stump-ai'
        // Docker CLI talks to the mounted socket from inside this agent.
        DOCKER_HOST       = 'unix:///var/run/docker.sock'
    }

    stage('Checkout') {
    steps {
        checkout scm
        script {
            env.IMAGE_TAG = "${env.BUILD_NUMBER}-${sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()}"
            env.IMAGE_FULL = "${env.IMAGE_NAME}:${env.IMAGE_TAG}"
        }
    }
}

        stage('Static checks') {
            agent {
                docker {
                    image 'python:3.12-slim-bookworm'
                    args '-u root:root -v "$WORKSPACE":/work -w /work'
                    reuseNode true
                }
            }
            steps {
                sh '''
                    python -m pip install --upgrade pip
                    # Fail fast on a broken environment before spending build time
                    python -m pip install --requirement requirements.txt
                    python -m pip check
                '''
                sh 'python -m compileall -q config matches players predictions analytics dashboard run.py'
                sh 'python manage.py check'
                sh 'python manage.py makemigrations --check --dry-run'
            }
        }

        stage('Test') {
            agent {
                docker {
                    image 'python:3.12-slim-bookworm'
                    args '-u root:root -v "$WORKSPACE":/work -w /work'
                    reuseNode true
                }
            }
            steps {
                sh '''
                    python manage.py test --testrunner=config.junit_runner.JunitXmlRunner
                '''
            }
            post {
                always {
                    // Surface the 44-test result in the Jenkins UI.
                    junit 'test-results.xml'
                }
            }
        }

        stage('Build image') {
            steps {
                sh '''
                    docker build \
                        --tag "${IMAGE_FULL}" \
                        --tag "${IMAGE_NAME}:latest" \
                        --label "jenkins.build=${BUILD_NUMBER}" \
                        --label "jenkins.commit=${GIT_COMMIT}" \
                        .
                '''
                sh 'docker images "${IMAGE_NAME}"'
            }
        }

        stage('Smoke test image') {
            steps {
                // Start the built image against a throwaway database and wait
                // for /health/ before anything is published or deployed.
                sh '''
                    docker network create stump-ci-net || true
                    docker run -d --name stump-ci-db \
                        --network stump-ci-net \
                        -e POSTGRES_DB=ipl -e POSTGRES_USER=ipl -e POSTGRES_PASSWORD=ci \
                        postgres:16-alpine
                    docker run -d --name stump-ci-web \
                        --network stump-ci-net \
                        -p 18000:8000 \
                        -e DB_ENGINE=postgresql \
                        -e DB_NAME=ipl -e DB_USER=ipl -e DB_PASSWORD=ci \
                        -e DB_HOST=stump-ci-db -e DB_PORT=5432 \
                        -e DJANGO_DEBUG=0 \
                        -e DJANGO_ALLOW_ALL_HOSTS=1 \
                        "${IMAGE_FULL}"
                '''
                script {
                    def healthy = false
                    for (int i = 0; i < 60; i++) {
                        def code = sh(
                            script: 'docker inspect -f \'{{.State.Health.Status}}\' stump-ci-web 2>/dev/null || echo none',
                            returnStdout: true
                        ).trim()
                        if (code == 'healthy') { healthy = true; break }
                        sleep(5)
                    }
                    if (!healthy) {
                        sh 'docker logs --tail 200 stump-ci-web || true'
                        error 'Container never became healthy'
                    }
                    sh 'curl --fail --silent http://127.0.0.1:18000/health/'
                }
            }
            post {
                always {
                    sh 'docker rm -f stump-ci-web stump-ci-db || true'
                    sh 'docker network rm stump-ci-net || true'
                }
            }
        }

        stage('Publish image') {
            steps {
                // Tag for a registry and push. Replace with your own registry.
                script {
                    if (env.REGISTRY) {
                        withCredentials([usernamePassword(
                            credentialsId: 'registry-credentials',
                            usernameVariable: 'REG_USER',
                            passwordVariable: 'REG_PASS'
                        )]) {
                            sh """
                                docker tag "${IMAGE_FULL}" "${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
                                docker login "${REGISTRY}" -u "${REG_USER}" --password-stdin <<< '${REG_PASS}'
                                docker push "${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
                            """
                        }
                    } else {
                        echo 'No REGISTRY set - keeping the image on the agent only.'
                    }
                }
            }
        }

        stage('Deploy') {
            steps {
                script {
                    if (env.DEPLOY_HOST) {
                        withCredentials([sshUserPrivateKey(
                            credentialsId: 'stump-ai-deploy',
                            keyFileVariable: 'SSH_KEY',
                            usernameVariable: 'DEPLOY_USER'
                        )]) {
                            // The image travels by registry; the host only
                            // needs `docker compose pull && up -d`.
                            sh """
                                scp -i "\${SSH_KEY}" docker-compose.yml ${DEPLOY_USER}@${DEPLOY_HOST}:/opt/stump-ai/docker-compose.yml
                                ssh -i "\${SSH_KEY}" ${DEPLOY_USER}@${DEPLOY_HOST} '
                                    cd /opt/stump-ai &&
                                    docker compose pull &&
                                    docker compose up -d --remove-orphans &&
                                    docker compose ps
                                '
                            """
                        }
                    } else {
                        echo 'No DEPLOY_HOST set - deploying on this agent instead.'
                        sh '''
                            docker compose -p "${COMPOSE_PROJECT}" down --remove-orphans || true
                            docker compose -p "${COMPOSE_PROJECT}" up -d --build web
                            docker compose -p "${COMPOSE_PROJECT}" ps
                        '''
                    }
                }
            }
        }
    }

    post {
        success {
            echo "Deployed ${IMAGE_FULL}"
        }
        failure {
            echo 'Build failed. Check the stage logs above.'
        }
        always {
            // Never leave the smoke-test containers behind.
            sh 'docker rm -f stump-ci-web stump-ci-db 2>/dev/null || true'
            cleanWs()
        }
    }
}
