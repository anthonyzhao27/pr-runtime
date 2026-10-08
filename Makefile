# Common operations. Image builds and eval runs are meant to run on the driver box (scripts/driver.sh), not a laptop.
ECR := 511572480852.dkr.ecr.us-east-1.amazonaws.com/pr-runtime
NS  := pr-runtime

.PHONY: build-controller build-runner push rollout eval-env

build-controller:
	docker build --platform linux/arm64 -f controller/Dockerfile -t $(ECR)/controller:dev .

build-runner:
	docker build --platform linux/arm64 -t $(ECR)/runner:dev runner/

push:
	aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin $(ECR)
	docker push $(ECR)/controller:dev
	docker push $(ECR)/runner:dev

rollout:
	helm upgrade --install pr-runtime deploy/chart --namespace $(NS) --wait --timeout 4m
	kubectl rollout restart deploy/controller -n $(NS)
	kubectl rollout status deploy/controller -n $(NS) --timeout=180s

eval-env:
	uv venv -q controller/.venv --python 3.12 && uv pip install -q --python controller/.venv/bin/python -r controller/pyproject.toml
