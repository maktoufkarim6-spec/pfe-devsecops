{{/* Nom de l'application = nom de la release = namespace (un dossier applications/<nom>/) */}}
{{- define "app.nom" -}}{{ .Release.Name }}{{- end -}}

{{- define "app.secret" -}}{{ .Release.Name }}-secrets{{- end -}}

{{/* Domaine : celui de values.yaml, sinon <nom>.<ip-avec-tirets>.sslip.io */}}
{{- define "app.domaine" -}}
{{- if .Values.domaine -}}
{{ .Values.domaine }}
{{- else if .Values.plateforme.ip -}}
{{ .Release.Name }}.{{ .Values.plateforme.ip | replace "." "-" }}.sslip.io
{{- else -}}
{{ fail "domaine vide et plateforme.ip inconnue : impossible de choisir une adresse web" }}
{{- end -}}
{{- end -}}

{{/* HTTPS : activé sur la plateforme ET vrai domaine choisi (Let's Encrypt ne délivre rien pour sslip.io ici) */}}
{{- define "app.https" -}}
{{- if and (eq (toString .Values.plateforme.https) "true") .Values.domaine -}}oui{{- end -}}
{{- end -}}

{{/* Règles d'Ingress (un chemin par composant public) ; argument : dict domaine, publics */}}
{{- define "app.regles" -}}
rules:
  # Règle par nom d'hôte : prioritaire sur l'application cobaye (règle sans hôte)
  - host: {{ .domaine }}
    http:
      paths:
        {{- range .publics }}
        - path: {{ .chemin | default "/" }}
          pathType: Prefix
          backend:
            service:
              name: {{ .nom }}
              port:
                number: {{ .port }}
        {{- end }}
{{- end -}}

{{- define "app.labels" -}}
app.kubernetes.io/part-of: {{ .Release.Name }}
app.kubernetes.io/managed-by: plateforme-pfe
{{- end -}}

{{/* Placement sur les nœuds choisis (étiquette pfe.noeud posée par deploy.sh) */}}
{{- define "app.placement" -}}
{{- if .Values.noeuds }}
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: pfe.noeud
              operator: In
              values: {{ toJson .Values.noeuds }}
{{- end }}
{{- end -}}

{{/* Contrôles faits avant tout déploiement : une erreur ici bloque la synchronisation Argo CD */}}
{{- define "app.verifier" -}}
{{- if not (regexMatch "^[a-z][a-z0-9-]{1,30}[a-z0-9]$" .Release.Name) -}}
{{ fail (printf "nom d'application invalide : %q (minuscules, chiffres, tirets, 3 à 32 caractères)" .Release.Name) }}
{{- end -}}
{{- if has .Release.Name (list "default" "kube-system" "kube-public" "kube-node-lease" "argocd" "plateforme") -}}
{{ fail (printf "nom d'application réservé : %q" .Release.Name) }}
{{- end -}}
{{- if ne .Release.Namespace .Release.Name -}}
{{ fail "le namespace doit porter le nom de l'application" }}
{{- end -}}
{{- if not .Values.composants -}}
{{ fail "aucun composant déclaré dans values.yaml" }}
{{- end -}}
{{- range .Values.composants -}}
{{- if not (regexMatch "^[a-z][a-z0-9-]{0,30}[a-z0-9]$" (.nom | default "")) -}}
{{ fail (printf "composant sans nom valide : %v" .) }}
{{- end -}}
{{- if eq .nom "base" -}}
{{ fail "« base » est réservé à la base PostgreSQL" }}
{{- end -}}
{{- if not (regexMatch ":[A-Za-z0-9_][A-Za-z0-9_.-]*$|@sha256:[a-f0-9]{64}$" (.image | default "")) -}}
{{ fail (printf "composant %s : image sans tag figé (%q)" .nom .image) }}
{{- end -}}
{{- if hasSuffix ":latest" .image -}}
{{ fail (printf "composant %s : le tag « latest » est interdit (version non traçable)" .nom) }}
{{- end -}}
{{- if not .port -}}
{{ fail (printf "composant %s : port manquant" .nom) }}
{{- end -}}
{{- if eq (int (.utilisateur | default 0)) 0 -}}
{{ fail (printf "composant %s : utilisateur non root obligatoire (UID > 0)" .nom) }}
{{- end -}}
{{- end -}}
{{- end -}}
