# Jenkins de la plateforme, reproductible : version figée, clients Docker et Trivy inclus (sur le premier parc,
# Trivy avait été ajouté à la main DANS le conteneur et aurait disparu à sa recréation), configuration par
# fichier (Configuration as Code) : aucun assistant d'installation, aucun réglage manuel.
FROM jenkins/jenkins:2.568.2-lts-jdk21

USER root
ARG DOCKER_VERSION=29.7.2
ARG TRIVY_VERSION=0.74.0
# Clients seulement : Jenkins utilise le Docker de l'hôte (socket monté). Archive Trivy vérifiée par son empreinte.
RUN set -eux; \
    curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" \
      | tar -xz -C /usr/local/bin --strip-components=1 docker/docker; \
    cd /tmp; \
    curl -fsSLO "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz"; \
    curl -fsSL "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}/trivy_${TRIVY_VERSION}_checksums.txt" \
      | grep " trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz$" | sha256sum -c -; \
    tar -xzf "trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz" -C /usr/local/bin trivy; \
    rm -f /tmp/trivy_*; \
    docker --version; trivy --version

USER jenkins
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false" \
    CASC_JENKINS_CONFIG=/var/jenkins_conf/jenkins.yaml \
    SECRETS=/run/secrets
