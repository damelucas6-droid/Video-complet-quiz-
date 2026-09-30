# API Backend - Générateur de Vidéos Quiz Verticaux (Shorts / TikTok / Reels)

API backend haute performance développée avec **FastAPI**, **MoviePy v2.x**, **FFmpeg** et **edge-tts**, conçue pour automatiser la création de vidéos de quiz verticaux de **60 secondes** (format 9:16, 1080x1920, 30 fps).

Le traitement vidéo s'exécute de façon asynchrone via `fastapi.BackgroundTasks` pour renvoyer une réponse HTTP immédiate, garantissant la fluidité du service.

---

## 📁 Arborescence du Projet

```text
Video ANTIGRAVITY QUIZ/
├── assets/
│   ├── backgrounds/
│   │   └── default.mp4            # Fond vidéo vertical par défaut (1080x1920)
│   ├── fonts/
│   │   └── Montserrat-Black.ttf    # Police typographique percutante
│   └── sfx/
│       ├── correct.mp3            # SFX validation bonne réponse (déclenché à 8s)
│       ├── music.mp3              # Musique de fond (bouclée à 60s, atténuée à 18%)
│       └── tick.mp3               # SFX compte à rebours (joué de 3s à 8s)
├── output/                        # Fichiers MP4 finaux générés (téléchargeables)
├── temp/                          # Fichiers audio TTS temporaires (nettoyés automatiquement)
├── init_assets.py                 # Initialisation automatique des assets par défaut
├── main.py                        # Application FastAPI, modèles Pydantic v2 & pipeline MoviePy v2.x
├── sample_payload.json            # Payload d'exemple contenant 5 questions
├── test_api.py                    # Suite de tests unitaires (validation Pydantic & endpoints)
├── verify_render.py               # Test d'intégration bout-en-bout du rendu vidéo
├── requirements.txt               # Liste des dépendances pip
└── README.md                      # Guide d'installation et de documentation
```

---

## ⏱️ Structure Chronologique d'une Vidéo (60s)

Une vidéo complète est composée de **5 séquences de 12 secondes** :

```text
[ 0s ──────── 3s ──────── 8s ──────── 12s ]
  │           │           │             │
  ├─ Énoncé   │           │             │
  │  + Voix   │           │             │
  │           ├─ 4 Choix  │             │
  │           │  + Tick   │             │
  │           │           ├─ Révélation │
  │           │           │  (Vert/Gris)│
  │           │           │  + Chime    │
  │           │           │             └─ Transition question suivante
```

1. **0s à 3s** : Affichage centré haut du texte de la question + lecture audio de l'énoncé par la voix off française (`edge-tts`).
2. **3s à 8s** : Apparition des 4 choix verticaux (A, B, C, D) avec fond par défaut (`#1E1E2E`) + son de compte à rebours (`tick.mp3`).
3. **8s à 12s** : L'option gagnante bascule en vert (`#2ECC71`), les autres passent en gris (`#555555`), et la voix annonce la lettre et le texte de la bonne réponse (par exemple : « La bonne réponse est B : Mercure »), avec le son de confirmation (`correct.mp3`).
4. **Transition** : Enchaînement immédiat sur la question suivante (Total = 60s).
5. **Mixage Audio** : Voix off + SFX superposés à la musique de fond bouclée et atténuée à 18% du volume.

La synthèse vocale utilise `edge-tts` et nécessite une connexion Internet pendant la génération. Pour chaque question, renseignez `reponse_correcte` avec `A`, `B`, `C` ou `D` et assurez-vous que le choix correspondant (`choix_a` à `choix_d`) contient le texte à faire prononcer.

---

## 🛠️ Installation sous Windows 10

### 1. Cloner ou naviguer dans le dossier du projet
```powershell
cd "c:\Users\Pc\Desktop\Travail Api\Video ANTIGRAVITY QUIZ"
```

### 2. Installer les dépendances Python
```powershell
pip install -r requirements.txt
```

*(Ou installation manuelle directe)* :
```powershell
pip install fastapi "uvicorn[standard]" pydantic moviepy edge-tts imageio-ffmpeg pillow numpy
```

### 3. Initialiser les assets par défaut (Optionnel mais recommandé)
Cette commande génère automatiquement les sons SFX, télécharge la police Montserrat et produit un arrière-plan vidéo vertical :
```powershell
python init_assets.py
```

---

## 🚀 Démarrage du Serveur FastAPI

Pour lancer l'API en local sous Windows 10 :

```powershell
uvicorn main:app --host 127.0.0.1 --port 8000
```

- **Documentation interactive Swagger** : [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **Documentation ReDoc** : [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

---

## 📡 Utilisation de l'API

### 1. Générer une vidéo (`POST /generate-quiz`)

Envoyez une requête POST avec le JSON contenant exactement 5 questions :

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/generate-quiz" -Method Post -InFile "sample_payload.json" -ContentType "application/json"
```

**Réponse HTTP 202 immédiate** :
```json
{
  "task_id": "a8f3b2c1",
  "status": "queued",
  "message": "Le rendu vidéo a été lancé en arrière-plan avec succès.",
  "status_url": "/quiz-status/a8f3b2c1",
  "download_url": "/download/a8f3b2c1",
  "estimated_duration": "60 secondes de vidéo (5 questions x 12 secondes)",
  "output_file": "output/quiz_a8f3b2c1.mp4"
}
```

### 2. Consulter l'avancement (`GET /quiz-status/{task_id}`)

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/quiz-status/a8f3b2c1" -Method Get
```

**Réponse en cours de rendu** :
```json
{
  "task_id": "a8f3b2c1",
  "status": "rendering_video",
  "message": "Assemblage visuel MoviePy v2.x et encodage FFmpeg (preset fast, 4 threads)...",
  "progress": 50
}
```

**Réponse une fois terminé** :
```json
{
  "task_id": "a8f3b2c1",
  "status": "completed",
  "message": "Vidéo générée avec succès !",
  "progress": 100,
  "output_file": "output/quiz_a8f3b2c1.mp4",
  "file_size_mb": 14.85,
  "completed_at": "2026-09-27T22:25:00Z"
}
```

### 3. Télécharger la vidéo terminée (`GET /download/{task_id}`)

Rendez-vous directement dans votre navigateur sur :
`http://127.0.0.1:8000/download/a8f3b2c1`
ou en PowerShell :
```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8000/download/a8f3b2c1" -OutFile "mon_quiz.mp4"
```

---

## 🧪 Tests Automatisés

- **Validation des schémas Pydantic et des codes de retour HTTP** :
  ```powershell
  python test_api.py
  ```
- **Test d'intégration complet avec rendu vidéo** :
  ```powershell
  python verify_render.py
  ```

---

## ⚙️ Optimisations & Stabilité Windows 10
- **Gestion des verrous fichiers** : Libération explicite de tous les clips (`.close()`) et passage du ramasse-miettes (`gc.collect()`) pour éviter les erreurs `PermissionError: [WinError 32]` sous Windows.
- **Ressources CPU / RAM** : Export avec `threads=4` et `preset='fast'` pour préserver les ressources machine.
- **Police résiliente** : Si la police `Montserrat-Black.ttf` n'est pas trouvée, un fallback automatique sur les polices système Windows (`arialbd.ttf`, `arial.ttf`) s'active sans interrompre le service.
