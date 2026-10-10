
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
        IMAGE_NAME      = 'stump-ai'
        IMAGE_TAG       = ''
        IMAGE_FULL      = ''
        COMPOSE_PROJECT = 'stump-ai'
        DOCKER_HOST     = 'unix:///var/run/docker.sock'
    }

    stages {

        // 1. CHECKOUT
        stage('Checkout') {
            steps {
                checkout scm

                script {
                    env.GIT_COMMIT = sh(
                        script: 'git rev-parse --short HEAD',
                        returnStdout: true
                    ).trim()

                    env.IMAGE_TAG =
                        "${env.BUILD_NUMBER}-${env.GIT_COMMIT}"

                    env.IMAGE_FULL =
                        "${env.IMAGE_NAME}:${env.IMAGE_TAG}"

                    echo "Building image: ${env.IMAGE_FULL}"
                }
            }
        }

        // 2. STATIC CHECKS
        stage('Static checks') {
            agent {
                docker {
                    image 'python:3.12-slim-bookworm'
                    args '-u root:root'
                    reuseNode true
                }
            }

            steps {
                sh '''
                    set -eu

                    python -m pip install --upgrade pip
                    python -m pip install -r requirements.txt
                    python -m pip check
                '''

                sh '''
                    python -m compileall -q \
                        config matches players predictions \
                        analytics dashboard run.py
                '''

                sh 'python manage.py check'

                sh 'python manage.py makemigrations --check --dry-run'
            }
        }

        // 3. TEST
        stage('Test') {
            agent {
                docker {
                    image 'python:3.12-slim-bookworm'
                    args '-u root:root'
                    reuseNode true
                }
            }

            steps {
                sh '''
                    set -eu
                    python manage.py test \
                        --testrunner=config.junit_runner.JunitXmlRunner
                '''
            }

            post {
                always {
                    junit(
                        testResults: 'test-results.xml',
                        allowEmptyResults: true
                    )
                }
            }
        }

        // 4. BUILD DOCKER IMAGE
        stage('Build image') {
            steps {
                sh '''
                    set -eu

                    docker build \
                        --tag "${IMAGE_FULL}" \
                        --tag "${IMAGE_NAME}:latest" \
                        --label "jenkins.build=${BUILD_NUMBER}" \
                        --label "jenkins.commit=${GIT_COMMIT}" \
                        .

                    docker images "${IMAGE_NAME}"
                '''
            }
        }

        // 5. SMOKE TEST
        stage('Smoke test image') {
            steps {
                script {
                    try {
                        sh '''
                            set -eu

                            docker network create stump-ci-net

                            docker run -d \
                                --name stump-ci-db \
                                --network stump-ci-net \
                                -e POSTGRES_DB=ipl \
                                -e POSTGRES_USER=ipl \
                                -e POSTGRES_PASSWORD=ci \
                                postgres:16-alpine

                            # Wait for PostgreSQL to become ready.
                            db_ready=0

                            for i in $(seq 1 30); do
                                if docker exec stump-ci-db \
                                    pg_isready -U ipl -d ipl; then
                                    db_ready=1
                                    break
                                fi

                                sleep 2
                            done

                            if [ "$db_ready" -ne 1 ]; then
                                docker logs stump-ci-db || true
                                echo "PostgreSQL did not become ready"
                                exit 1
                            fi

                            docker run -d \
                                --name stump-ci-web \
                                --network stump-ci-net \
                                -p 18000:8000 \
                                -e DB_ENGINE=postgresql \
                                -e DB_NAME=ipl \
                                -e DB_USER=ipl \
                                -e DB_PASSWORD=ci \
                                -e DB_HOST=stump-ci-db \
                                -e DB_PORT=5432 \
                                -e DJANGO_DEBUG=0 \
                                -e DJANGO_ALLOW_ALL_HOSTS=1 \
                                "${IMAGE_FULL}"
                        '''

                        // Test the HTTP endpoint directly instead of
                        // relying on a Docker HEALTHCHECK being defined.
                        sh '''
                            set -eu

                            ready=0

                            for i in $(seq 1 60); do
                                if curl --fail --silent \
                                    http://127.0.0.1:18000/health/; then
                                    echo ""
                                    echo "Smoke test passed."
                                    ready=1
                                    break
                                fi

                                sleep 5
                            done

                            if [ "$ready" -ne 1 ]; then
                                docker logs --tail 200 stump-ci-web || true
                                echo "Application health check failed"
                                exit 1
                            fi
                        '''
                    } finally {
                        sh '''
                            docker rm -f stump-ci-web stump-ci-db \
                                2>/dev/null || true

                            docker network rm stump-ci-net \
                                2>/dev/null || true
                        '''
                    }
                }
            }
        }

        // 6. PUBLISH IMAGE
        stage('Publish image') {
            when {
                expression {
                    return env.REGISTRY?.trim()
                }
            }

            steps {
                withCredentials([
                    usernamePassword(
                        credentialsId: 'registry-credentials',
                        usernameVariable: 'REG_USER',
                        passwordVariable: 'REG_PASS'
                    )
                ]) {
                    sh '''
                        set -eu

                        printf '%s' "$REG_PASS" |
                            docker login "$REGISTRY" \
                                --username "$REG_USER" \
                                --password-stdin

                        docker tag \
                            "${IMAGE_FULL}" \
                            "${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

                        docker push \
                            "${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

                        docker logout "$REGISTRY" || true
                    '''
                }
            }
        }

        // 7. DEPLOY
        stage('Deploy') {
            steps {
                script {
                    if (env.DEPLOY_HOST?.trim()) {

                        if (!env.REGISTRY?.trim()) {
                            error(
                                'Remote deployment requires REGISTRY ' +
                                'to be configured.'
                            )
                        }

                        withCredentials([
                            sshUserPrivateKey(
                                credentialsId: 'stump-ai-deploy',
                                keyFileVariable: 'SSH_KEY',
                                usernameVariable: 'DEPLOY_USER'
                            )
                        ]) {
                            sh '''
                                set -eu

                                # The remote host must already have
                                # Docker, Compose, and registry access.
                                ssh -i "$SSH_KEY" \
                                    -o StrictHostKeyChecking=accept-new \
                                    "$DEPLOY_USER@$DEPLOY_HOST" \
                                    'mkdir -p /opt/stump-ai'

                                scp -i "$SSH_KEY" \
                                    -o StrictHostKeyChecking=accept-new \
                                    docker-compose.yml \
                                    "$DEPLOY_USER@$DEPLOY_HOST:/opt/stump-ai/docker-compose.yml"

                                ssh -i "$SSH_KEY" \
                                    -o StrictHostKeyChecking=accept-new \
                                    "$DEPLOY_USER@$DEPLOY_HOST" '
                                        set -eu
                                        cd /opt/stump-ai
                                        docker compose pull
                                        docker compose up -d --remove-orphans
                                        docker compose ps
                                    '
                            '''
                        }

                    } else {
                        echo 'DEPLOY_HOST is not configured.'
                        echo 'Deploying with local Docker Compose.'

                        sh '''
                            set -eu

                            docker compose \
                                -p "${COMPOSE_PROJECT}" \
                                down --remove-orphans || true

                            docker compose \
                                -p "${COMPOSE_PROJECT}" \
                                up -d --build web

                            docker compose \
                                -p "${COMPOSE_PROJECT}" ps
                        '''
                    }
                }
            }
        }
    }

    // POST-BUILD ACTIONS
    post {
        success {
            echo "Pipeline completed successfully: ${env.IMAGE_FULL}"
        }

        failure {
            echo 'Pipeline failed. Check the failed stage logs.'
        }

        always {
            script {
                sh '''
                    docker rm -f stump-ci-web stump-ci-db \
                        2>/dev/null || true

                    docker network rm stump-ci-net \
                        2>/dev/null || true
                '''

                cleanWs()
            }
        }
    }
}
