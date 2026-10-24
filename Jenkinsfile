pipeline {
    agent any

    options {
        timestamps()
    }

    environment {
        IMAGE_NAME = 'campusfix:ci'
        CONTAINER_NAME = 'campusfix'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                echo 'CampusFix source code checked out successfully.'
            }
        }

        stage('Build') {
            steps {
                echo 'Building CampusFix Docker image...'
                sh 'docker build -t campusfix:ci .'
            }
        }

        stage('Test') {
            steps {
                echo 'Running automated tests...'
                sh 'docker run --rm -e APP_ENV=testing -e SECRET_KEY=test-only-secret-key-for-ci --entrypoint python campusfix:ci -m pytest tests/ -v'
            }
        }

        stage('Deploy') {
            steps {
                echo 'Deploying CampusFix...'
                sh '''
                    docker rm -f campusfix || true
                    docker run -d \
                      --name campusfix \
                      --restart unless-stopped \
                      -p 5000:5000 \
                      --env-file .env \
                      -e APP_ENV=production \
                      -e CAMPUSFIX_DB=/data/campusfix.db \
                      -e UPLOAD_FOLDER=/data/uploads \
                      -e SESSION_COOKIE_SECURE=false \
                      -v campusfix-data:/data \
                      campusfix:ci
                '''
            }
        }
    }

    post {
        success {
            echo 'CampusFix CI/CD pipeline completed successfully!'
        }
        failure {
            echo 'Pipeline failed. Check the stage logs for details.'
        }
    }
}