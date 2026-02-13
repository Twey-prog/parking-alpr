# Parking ALPR

Système de reconnaissance automatique de plaques d'immatriculation (ALPR) pour parking utilisant un ESP32-CAM, Home Assistant, Frigate et CodeProject.AI.

## Architecture

Le système est composé de plusieurs services Docker :
- **ESPHome** : Gestion du firmware de l'ESP32-CAM
- **Mosquitto** : Broker MQTT pour la communication entre services
- **Frigate** : Traitement vidéo et détection d'objets
- **Home Assistant** : Interface d'automatisation et de contrôle
- **CodeProject.AI** : Reconnaissance de plaques d'immatriculation

## Qu'est-ce qu'ESPHome ?

ESPHome est un système de configuration pour microcontrôleurs ESP8266/ESP32 qui permet de créer des firmwares personnalisés via de simples fichiers YAML. Il s'intègre parfaitement avec Home Assistant et permet de :

- Configurer des capteurs et caméras sans écrire de code C++
- Mettre à jour les dispositifs à distance (OTA - Over The Air)
- Créer une API native pour Home Assistant
- Gérer facilement le WiFi, les logs et les composants matériels

Dans ce projet, ESPHome contrôle l'ESP32-CAM qui capture les images de véhicules pour l'analyse ALPR.

## Installation

### Prérequis : Espace disque

⚠️ **Important** : Ce projet nécessite environ **10-12 GB** d'espace libre sur la partition `/var` (où Docker stocke ses données). CodeProject.AI à lui seul nécessite ~6 GB d'espace libre pour le téléchargement et l'extraction.

Vérifiez l'espace disponible :
```bash
df -h /var
```

### 1. Nettoyer Docker (recommandé avant l'installation)

Pour éviter les problèmes d'espace disque, nettoyez Docker avant de commencer :

```bash
# Nettoyer toutes les images et conteneurs inutilisés
docker system prune -a -f

# Vérifier l'espace libéré
df -h /var
```

### 2. Démarrer l'environnement

```bash
# Télécharger les images une par une (recommandé pour économiser l'espace)
docker pull eclipse-mosquitto:latest && docker system prune -f
docker pull ghcr.io/esphome/esphome:stable && docker system prune -f
docker pull ghcr.io/blakeblackshear/frigate:stable && docker system prune -f
docker pull ghcr.io/home-assistant/home-assistant:stable && docker system prune -f
docker pull codeproject/ai-server:latest && docker system prune -f

# Démarrer tous les services
docker compose up -d

# Vérifier l'état des services
docker compose ps

# Voir les logs
docker compose logs -f
```

**Alternative si manque d'espace** : Démarrer sans CodeProject.AI (voir section "Démarrage sans CodeProject.AI")

### 3. Configurer ESPHome

Avant de flasher l'ESP32-CAM, configurez vos identifiants dans `esphome/secrets.yaml` :

```yaml
wifi_ssid: "VOTRE_WIFI"
wifi_password: "VOTRE_MOT_DE_PASSE"
api_key: "VOTRE_CLE_API"
ota_password: "VOTRE_MOT_DE_PASSE_OTA"
```

## Commandes ESPHome

### Compiler le firmware

Compile le firmware sans le flasher (utile pour vérifier la configuration) :

```bash
cd esphome
docker run --rm -v "${PWD}":/config -it esphome/esphome compile esp-scanner.yaml
```

### Flash via USB (première installation)

Connectez l'ESP32-CAM via USB et flashez le firmware :

```bash
cd esphome
docker run --rm -v "${PWD}":/config --device=/dev/ttyUSB0 -it esphome/esphome run esp-scanner.yaml
```

**Note** : Si votre périphérique n'est pas `/dev/ttyUSB0`, vérifiez avec `ls /dev/tty*`

### Mise à jour OTA (Over The Air)

Une fois l'ESP32-CAM configuré et connecté au WiFi, vous pouvez le mettre à jour sans câble :

```bash
cd esphome
docker run --rm -v "${PWD}":/config --network host -it esphome/esphome run esp-scanner.yaml
```

### Via le service Docker ESPHome

Vous pouvez aussi accéder à l'interface web ESPHome :

```bash
# L'interface est disponible sur http://localhost:6052
# Les modifications peuvent être faites directement via l'interface
```

## Configuration

### Accès aux interfaces

- **Home Assistant** : http://localhost:8123
- **ESPHome** : http://localhost:6052
- **Frigate** : http://localhost:5000
- **CodeProject.AI** : http://localhost:32168
- **ESP32-CAM** : http://[IP_DE_ESP32]:8080 (une fois connecté au WiFi)

### Personnalisation

- Modifiez `esphome/esp-scanner.yaml` pour ajuster les paramètres de la caméra
- Configurez Frigate dans `frigate/config.yml` pour la détection
- Paramétrez Home Assistant dans `homeassistant/` pour les automatisations

## Commandes utiles

```bash
# Arrêter tous les services
docker compose down

# Redémarrer un service spécifique
docker compose restart esphome

# Voir les logs d'un service
docker compose logs -f frigate

# Mettre à jour les images Docker (avec nettoyage entre chaque)
docker pull eclipse-mosquitto:latest && docker system prune -f
docker pull ghcr.io/esphome/esphome:stable && docker system prune -f
docker pull ghcr.io/blakeblackshear/frigate:stable && docker system prune -f
docker pull ghcr.io/home-assistant/home-assistant:stable && docker system prune -f
docker pull codeproject/ai-server:latest && docker system prune -f
docker compose up -d

# Nettoyer régulièrement Docker pour libérer de l'espace
docker system prune -a -f
```

## Démarrage sans CodeProject.AI

Si vous manquez d'espace disque (partition `/var` < 15GB), vous pouvez démarrer sans CodeProject.AI :

```bash
# Démarrer uniquement les services essentiels
docker compose up -d mosquitto esphome frigate homeassistant

# Ou éditer docker-compose.yml pour commenter le service codeproject-ai
```

Alternatives pour l'ALPR :
- Utiliser Frigate avec un modèle de détection de plaques personnalisé
- Intégrer un service ALPR externe via API
- Installer CodeProject.AI sur une machine séparée avec plus d'espace

## Dépannage

### Problème d'espace disque

Si vous obtenez l'erreur "no space left on device" :

```bash
# Vérifier l'espace disponible
df -h /var

# Nettoyer Docker agressivement
docker system prune -a --volumes -f

# Supprimer les images inutilisées
docker image prune -a -f

# Si nécessaire, déplacer le répertoire Docker vers une partition plus grande
# (nécessite de modifier /etc/docker/daemon.json et redémarrer Docker)
```

### L'ESP32-CAM ne se connecte pas

1. Vérifiez les identifiants WiFi dans `esphome/secrets.yaml`
2. Vérifiez les logs : `docker-compose logs esphome`
3. Reconnectez via USB et reflashez le firmware

### Problèmes de détection USB

Si `/dev/ttyUSB0` n'est pas accessible :
```bash
# Vérifier les périphériques USB
ls /dev/tty*

# Ajouter votre utilisateur au groupe dialout
sudo usermod -a -G dialout $USER
# Puis déconnectez-vous et reconnectez-vous
```

