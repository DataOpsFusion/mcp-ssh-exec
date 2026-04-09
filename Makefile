SERVICE := ssh-exec
IMAGE := docker-local.homeserverlocal.com/$(SERVICE)/$(SERVICE):latest
PLATFORM := linux/amd64
REMOTE_HOST := devops@192.168.0.70
REMOTE_DIR := /opt/$(SERVICE)

.PHONY: build push deploy release

build:
	docker buildx build --platform $(PLATFORM) -t $(IMAGE) --load .

push:
	docker buildx build --platform $(PLATFORM) -t $(IMAGE) --push .

deploy:
	ssh $(REMOTE_HOST) "mkdir -p $(REMOTE_DIR)"
	scp docker-compose.yml hosts.yaml known_hosts $(REMOTE_HOST):$(REMOTE_DIR)/
	if [ -f .env ]; then scp .env $(REMOTE_HOST):$(REMOTE_DIR)/.env; else printf '%s\n' "warning: ssh-exec/.env not found; password-backed hosts will fail until secrets are provided"; fi
	ssh $(REMOTE_HOST) "cd $(REMOTE_DIR) && docker compose pull && docker compose up -d"

release: build push deploy
