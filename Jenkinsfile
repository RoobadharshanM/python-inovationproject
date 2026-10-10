
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
        PATH = '/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin'

        DOCKER_BIN = '/usr/local/bin/docker'

        IMAGE_NAME = 'stump-ai'
        IMAGE_TAG = ''
        IMAGE_FULL = ''
        GIT_COMMIT = ''
        COMPOSE_PROJECT = 'stump-ai'
    }

    stages {

        // 1. CHECKOUT AND INITIALIZE
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

                    echo "Git commit: ${env.GIT_COMMIT}"
                    echo "Building image: ${env.IMAGE_FULL}"
                }
            }
        }

        // 2. VERIFY LOCAL TOOLS
        stage('Verify tools') {
            steps {
                sh '''
                    set -eu

                    echo "Checking Python..."
                    command -v python3
                    python3 --version

                    echo "Checking Docker..."
                    test -x "$DOCKER_BIN"
                    "$DOCKER_BIN" --version
                    "$DOCKER_BIN" info

                    echo "Checking Docker Compose..."
                    "$DOCKER_BIN" compose version

                    echo "Checking project files..."
                    test -f requirements.txt
                    test -f manage.py
                    test -f Dockerfile

                    echo "Tool verification passed."
                '''
            }
        }

        // 3. INSTALL PYTHON DEPENDENCIES
        // Runs on the Jenkins Mac agent, not a Docker agent.
        stage('Install dependencies') {
            steps {
                sh '''
                    set -eu

                    python3 -m venv .venv

                    .venv/bin/python -m pip install \
                        --disable-pip-version-check \
                        --upgrade pip

                    .venv/bin/python -m pip install \
                        --disable-pip-version-check \
                        -r requirements.txt

                    .venv/bin/python -m pip check
                '''
            }
        }

        // 4. STATIC CHECKS
        stage('Static checks') {
            steps {
                sh '''
                    set -eu

                    .venv/bin/python -m compileall -q \
                        config matches players predictions \
                        analytics dashboard run.py

                    .venv/bin/python manage.py check

                    .venv/bin/python manage.py makemigrations \
                        --check --dry-run

                    echo "Static checks passed."
                '''
            }
        }

        // 5. DJANGO TESTS
        stage('Test') {
            steps {
                sh '''
                    set -eu

                    .venv/bin/python manage.py test \
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

        // 6. BUILD THE APPLICATION IMAGE
        stage('Build image') {
            steps {
                sh '''
                    set -eu

                    "$DOCKER_BIN" build \
                        --tag "$IMAGE_FULL" \
                        --tag "$IMAGE_NAME:latest" \
                        --label "jenkins.build=$BUILD_NUMBER" \
                        --label "jenkins.commit=$GIT_COMMIT" \
                        .

                    "$DOCKER_BIN" image inspect "$IMAGE_FULL"

                    echo "Docker image built successfully."
                '''
            }
        }

        // 7. RUN THE APPLICATION AND CHECK ITS HEALTH
        stage('Smoke test image') {
            steps {
                script {
                    try {
                        sh '''
                            set -eu

                            # Remove leftovers from an interrupted build.
                            "$DOCKER_BIN" rm -f \
                                stump-ci-web stump-ci-db \
                                2>/dev/null || true

                            "$DOCKER_BIN" network rm \
                                stump-ci-net 2>/dev/null || true

                            "$DOCKER_BIN" network create stump-ci-net

                            "$DOCKER_BIN" run -d \
                                --name stump-ci-db \
                                --network stump-ci-net \
                                -e POSTGRES_DB=ipl \
                                -e POSTGRES_USER=ipl \
                                -e POSTGRES_PASSWORD=ci \
                                postgres:16-alpine

                            db_ready=0

                            for i in $(seq 1 30); do
                                if "$DOCKER_BIN" exec stump-ci-db \
                                    pg_isready -U ipl -d ipl; then
                                    db_ready=1
                                    break
                                fi

                                sleep 2
                            done

                            if [ "$db_ready" -ne 1 ]; then
                                "$DOCKER_BIN" logs stump-ci-db || true
                                echo "PostgreSQL failed to start."
                                exit 1
                            fi

                            "$DOCKER_BIN" run -d \
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
                                "$IMAGE_FULL"
                        '''

                        sh '''
                            set -eu

                            ready=0

                            for i in $(seq 1 60); do
                                if curl --fail --silent \
                                    http://127.0.0.1:18000/health/; then
                                    echo ""
                                    echo "Application health check passed."
                                    ready=1
                                    break
                                fi

                                sleep 5
                            done

                            if [ "$ready" -ne 1 ]; then
                                "$DOCKER_BIN" logs \
                                    --tail 200 stump-ci-web || true

                                echo "Application health check failed."
                                exit 1
                            fi
                        '''
                    } finally {
                        sh '''
                            "$DOCKER_BIN" rm -f \
                                stump-ci-web stump-ci-db \
                                2>/dev/null || true

                            "$DOCKER_BIN" network rm \
                                stump-ci-net 2>/dev/null || true
                        '''
                    }
                }
            }
        }

        // 8. OPTIONAL REGISTRY PUBLISH
        // Configure REGISTRY and registry-credentials in Jenkins
        // to enable this stage.
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
                            "$DOCKER_BIN" login "$REGISTRY" \
                                --username "$REG_USER" \
                                --password-stdin

                        "$DOCKER_BIN" tag \
                            "$IMAGE_FULL" \
                            "$REGISTRY/$IMAGE_NAME:$IMAGE_TAG"

                        "$DOCKER_BIN" push \
                            "$REGISTRY/$IMAGE_NAME:$IMAGE_TAG"

                        "$DOCKER_BIN" logout "$REGISTRY" || true
                    '''
                }
            }
        }

        // 9. DEPLOY
        stage('Deploy') {
            steps {
                script {
                    if (env.DEPLOY_HOST?.trim()) {
                        if (!env.REGISTRY?.trim()) {
                            error(
                                'Remote deployment requires REGISTRY.'
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
                        echo 'Deploying locally using Docker Compose.'

                        sh '''
                            set -eu

                            "$DOCKER_BIN" compose \
                                -p "$COMPOSE_PROJECT" \
                                down --remove-orphans || true

                            "$DOCKER_BIN" compose \
                                -p "$COMPOSE_PROJECT" \
                                up -d --build web

                            "$DOCKER_BIN" compose \
                                -p "$COMPOSE_PROJECT" ps
                        '''
                    }
                }
            }
        }
    }

    // 10. CLEANUP
    post {
        success {
            echo "Pipeline completed successfully: ${env.IMAGE_FULL}"
        }

        failure {
            echo 'Pipeline failed. Review the first failing stage.'
        }

        always {
            sh '''
                "$DOCKER_BIN" rm -f \
                    stump-ci-web stump-ci-db \
                    2>/dev/null || true

                "$DOCKER_BIN" network rm \
                    stump-ci-net 2>/dev/null || true
            '''

            cleanWs()
        }
    }
}
