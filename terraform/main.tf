variable "campusfix_secret_key" {
  description = "Secret key used by CampusFix"
  type        = string
  sensitive   = true
}


terraform {
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }
}

provider "docker" {}



resource "docker_volume" "campusfix_data" {
  name = "campusfix-terraform-data"
}

resource "docker_container" "campusfix" {
  name  = "campusfix-terraform"
  image = "campusfix:1.0"

  restart = "unless-stopped"

  ports {
    internal = 5000
    external = 5001
  }

  env = [
    "APP_ENV=production",
    "SECRET_KEY=${var.campusfix_secret_key}",
    "CAMPUSFIX_DB=/data/campusfix.db",
    "UPLOAD_FOLDER=/data/uploads",
    "SESSION_COOKIE_SECURE=false"
  ]

  volumes {
    volume_name    = docker_volume.campusfix_data.name
    container_path = "/data"
  }
}
