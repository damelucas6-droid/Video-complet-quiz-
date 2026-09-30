"""
API Backend d'Automatisation de Vidéos de Quiz Verticaux (Shorts / TikTok / Reels)
Format : 1080x1920 (9:16), 30 fps, durée 60 secondes (5 questions x 12 secondes).
Technologies : FastAPI, MoviePy v2.x, FFmpeg, edge-tts (voix française fr-FR-HenriNeural).
Environnement : Windows 10 (gestion stricte des chemins Pathlib, encodage UTF-8).
"""

import asyncio
import gc
import logging
from pathlib import Path
import time
from typing import Any
import uuid

import edge_tts
from fastapi import BackgroundTasks, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    TextClip,
    VideoFileClip,
    afx,
    concatenate_videoclips,
    vfx,
)
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Configuration de base & Logs
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("QuizVideoAPI")

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
SFX_DIR = ASSETS_DIR / "sfx"
BG_DIR = ASSETS_DIR / "backgrounds"
TEMP_DIR = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "output"

# S'assurer que tous les répertoires critiques existent
for directory in (FONTS_DIR, SFX_DIR, BG_DIR, TEMP_DIR, OUTPUT_DIR):
    directory.mkdir(parents=True, exist_ok=True)

# Registre en mémoire pour le suivi des tâches asynchrones
TASK_REGISTRY: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Schémas de données Pydantic v2
# ---------------------------------------------------------------------------
class QuestionItem(BaseModel):
    """Représente une question individuelle et ses 4 choix de réponse."""
    question: str = Field(..., min_length=3, description="Énoncé textuel de la question")
    choix_a: str = Field(..., min_length=1, description="Proposition A")
    choix_b: str = Field(..., min_length=1, description="Proposition B")
    choix_c: str = Field(..., min_length=1, description="Proposition C")
    choix_d: str = Field(..., min_length=1, description="Proposition D")
    reponse_correcte: str = Field(..., description="Lettre de la bonne réponse ('A', 'B', 'C' ou 'D')")

    @field_validator("reponse_correcte")
    @classmethod
    def validate_reponse_correcte(cls, value: str) -> str:
        clean_val = value.strip().upper()
        if clean_val not in {"A", "B", "C", "D"}:
            raise ValueError(
                f"La réponse correcte doit être 'A', 'B', 'C' ou 'D' (reçu : '{value}')."
            )
        return clean_val


class StyleConfig(BaseModel):
    """Configuration visuelle, typographique et sonore avec valeurs par défaut stables."""
    background_video: str = Field(
        default="assets/backgrounds/default.mp4",
        description="Chemin relatif ou absolu vers la vidéo d'arrière-plan",
    )
    music_file: str = Field(
        default="assets/sfx/music.mp3",
        description="Chemin vers le fichier audio de musique de fond",
    )
    tick_sfx: str = Field(
        default="assets/sfx/tick.mp3",
        description="Chemin vers le SFX de compte à rebours (joué de 3s à 8s)",
    )
    correct_sfx: str = Field(
        default="assets/sfx/correct.mp3",
        description="Chemin vers le SFX de bonne réponse (déclenché à 8s)",
    )
    font_path: str = Field(
        default="assets/fonts/Montserrat-Black.ttf",
        description="Chemin vers la police TrueType (.ttf)",
    )
    color_question: str = Field(
        default="#FFFFFF",
        description="Couleur hexadécimale du texte de la question",
    )
    color_box_default: str = Field(
        default="#1E1E2E",
        description="Couleur de base des boîtes de réponse (avant 8s)",
    )
    color_box_correct: str = Field(
        default="#2ECC71",
        description="Couleur de la réponse correcte révélée à 8s (vert)",
    )
    color_box_wrong: str = Field(
        default="#555555",
        description="Couleur des réponses erronées révélées à 8s (gris)",
    )
    voice_name: str = Field(
        default="fr-FR-HenriNeural",
        description="Voix edge-tts utilisée ('fr-FR-HenriNeural' ou 'fr-FR-DeniseNeural')",
    )


class QuizPayload(BaseModel):
    """Payload principal reçu par l'API contenant strictement 5 questions."""
    questions: list[QuestionItem] = Field(
        ...,
        description="Liste validée devant contenir strictement 5 questions pour 60s de vidéo.",
    )
    style: StyleConfig = Field(
        default_factory=StyleConfig,
        description="Paramètres visuels et audio (prend les valeurs par défaut si omis)",
    )

    @field_validator("questions")
    @classmethod
    def validate_exactement_5_questions(cls, value: list[QuestionItem]) -> list[QuestionItem]:
        if len(value) != 5:
            raise ValueError(
                f"Le quiz doit contenir strictement 5 questions (reçu : {len(value)}). "
                f"Chaque question dure 12s pour atteindre exactement 60 secondes."
            )
        return value


# ---------------------------------------------------------------------------
# Fonctions Utilitaires & Helpers
# ---------------------------------------------------------------------------
def hex_to_rgb(hex_code: str) -> tuple[int, int, int]:
    """Convertit un code hexadécimal '#RRGGBB' en tuple RGB (R, G, B) pour MoviePy ColorClip."""
    clean_hex = hex_code.strip().lstrip("#")
    if len(clean_hex) != 6:
        # Fallback de sécurité en cas de chaîne hex invalide
        return (30, 30, 46)
    return tuple(int(clean_hex[i : i + 2], 16) for i in (0, 2, 4))


def resolve_font_path(font_str: str) -> str:
    """Résout le chemin d'une police avec fallback Windows 10 si le fichier n'existe pas."""
    p = Path(font_str)
    if not p.is_absolute():
        p = BASE_DIR / p
    if p.exists() and p.is_file():
        return str(p)

    # Fallback système sous Windows
    windows_fonts = [
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
    ]
    for sys_font in windows_fonts:
        if sys_font.exists():
            logger.warning(
                f"Police '{font_str}' introuvable. Utilisation de la police système : {sys_font}"
            )
            return str(sys_font)

    return "Arial"


def resolve_asset_path(path_str: str, default_fallback: Path) -> Path | None:
    """Résout un chemin d'asset relatif ou absolu avec vérification de présence."""
    p = Path(path_str)
    if not p.is_absolute():
        p = BASE_DIR / p
    if p.exists() and p.is_file():
        return p
    if default_fallback.exists() and default_fallback.is_file():
        return default_fallback
    return None


def build_answer_announcement(question: QuestionItem) -> str:
    """Construit la phrase prononcée lors de la révélation de la réponse."""
    correct_answer = getattr(question, f"choix_{question.reponse_correcte.lower()}")
    return f"La bonne réponse est {question.reponse_correcte} : {correct_answer}."


def create_question_text_clip(question: str, font_file: str, color: str) -> TextClip:
    """Réduit la police jusqu'à ce que la question tienne sans toucher le cadre."""
    margin = 16
    for font_size in range(46, 26, -2):
        clip = TextClip(
            text=question,
            font=font_file,
            font_size=font_size,
            color=color,
            size=(960, 280),
            method="caption",
            text_align="center",
            duration=12.0,
        ).with_start(0.0).with_position(("center", 230))
        mask = clip.mask.get_frame(0)
        if not (
            mask[:, :margin].any()
            or mask[:, -margin:].any()
            or mask[:margin, :].any()
            or mask[-margin:, :].any()
        ):
            return clip
        clip.close()
    raise ValueError("La question est trop longue pour tenir dans la zone d'affichage.")


async def generate_tts_for_questions(
    task_id: str,
    questions: list[QuestionItem],
    voice_name: str,
) -> tuple[list[Path], list[Path]]:
    """Génère les fichiers audio des questions et de leurs bonnes réponses."""
    question_tts_paths: list[Path] = []
    answer_tts_paths: list[Path] = []
    for idx, q_item in enumerate(questions):
        audio_items = [
            (q_item.question, TEMP_DIR / f"{task_id}_q{idx}.mp3", "+10%", question_tts_paths),
            (
                build_answer_announcement(q_item),
                TEMP_DIR / f"{task_id}_a{idx}.mp3",
                "+20%",
                answer_tts_paths,
            ),
        ]
        for text, output_tts_file, rate, output_paths in audio_items:
            communicate = edge_tts.Communicate(text=text, voice=voice_name, rate=rate)
            await communicate.save(str(output_tts_file))
            if not output_tts_file.exists() or output_tts_file.stat().st_size == 0:
                raise RuntimeError(f"Échec de la génération TTS pour la question {idx + 1}")
            output_paths.append(output_tts_file)
            logger.info(f"[{task_id}] TTS généré : {output_tts_file.name}")
    return question_tts_paths, answer_tts_paths


# ---------------------------------------------------------------------------
# Pipeline MoviePy v2.x : Assemblage des Calques et Rendu
# ---------------------------------------------------------------------------
def build_and_render_video(
    task_id: str,
    payload: QuizPayload,
    question_tts_paths: list[Path],
    answer_tts_paths: list[Path],
    output_filepath: Path,
) -> None:
    """
    Assemble les 5 séquences de 12 secondes et exporte la vidéo finale de 60 secondes en 1080x1920.
    Gère la libération des ressources et le nettoyage sous Windows 10.
    """
    clips_to_close: list[Any] = []
    style = payload.style

    # Résolution des assets
    font_file = resolve_font_path(style.font_path)
    bg_video_path = resolve_asset_path(style.background_video, BG_DIR / "default.mp4")
    music_path = resolve_asset_path(style.music_file, SFX_DIR / "music.mp3")
    tick_path = resolve_asset_path(style.tick_sfx, SFX_DIR / "tick.mp3")
    correct_path = resolve_asset_path(style.correct_sfx, SFX_DIR / "correct.mp3")

    logger.info(f"[{task_id}] Début de l'assemblage MoviePy avec police '{font_file}'")

    try:
        # 1. Préparation de la vidéo de fond (1080x1920)
        source_bg_clip: VideoFileClip | None = None
        if bg_video_path:
            try:
                raw_bg = VideoFileClip(str(bg_video_path))
                clips_to_close.append(raw_bg)
                # Redimensionnement vers 1080x1920 si différent
                if raw_bg.size != (1080, 1920):
                    source_bg_clip = raw_bg.resized((1080, 1920))
                    clips_to_close.append(source_bg_clip)
                else:
                    source_bg_clip = raw_bg
            except Exception as e:
                logger.warning(f"[{task_id}] Impossible de charger la vidéo de fond ({e}). Fallback solide.")
                source_bg_clip = None

        question_video_clips: list[CompositeVideoClip] = []
        all_sfx_audio_clips: list[Any] = []

        # 2. Construction des 5 séquences de 12 secondes (Total = 60s)
        for q_idx in range(5):
            q_item = payload.questions[q_idx]
            q_tts_path = question_tts_paths[q_idx]
            answer_tts_path = answer_tts_paths[q_idx]
            sequence_start_time = q_idx * 12.0  # Position temporelle absolue dans la vidéo finale

            # A. Fond vidéo de la séquence (12 secondes)
            if source_bg_clip is not None and source_bg_clip.duration > 0:
                sub_start = (q_idx * 12.0) % max(1.0, source_bg_clip.duration)
                sub_end = sub_start + 12.0
                if sub_end <= source_bg_clip.duration:
                    bg_seq = source_bg_clip.subclipped(sub_start, sub_end)
                else:
                    # Si la vidéo est plus courte, on boucle la portion
                    bg_seq = source_bg_clip.subclipped(0, min(12.0, source_bg_clip.duration)).with_effects(
                        [vfx.Loop(duration=12.0)]
                    )
            else:
                # Arrière-plan de secours uni élégant
                bg_seq = ColorClip(size=(1080, 1920), color=(18, 19, 32), duration=12.0)

            # Voile sombre semi-transparent pour maximiser le contraste et la lisibilité
            dark_overlay = (
                ColorClip(size=(1080, 1920), color=(0, 0, 0), duration=12.0)
                .with_opacity(0.35)
                .with_position((0, 0))
            )

            # B. Badge indicateur du numéro de la question (ex: "QUESTION 1 / 5")
            badge_text = (
                TextClip(
                    text=f"QUESTION  {q_idx + 1} / 5",
                    font=font_file,
                    font_size=32,
                    color="#F1C40F",
                    duration=12.0,
                )
                .with_start(0.0)
                .with_position(("center", 160))
            )

            # C. Question texte (0s à 12s, méthode 'caption' pour retour à la ligne automatique)
            question_text_clip = create_question_text_clip(
                q_item.question,
                font_file,
                style.color_question,
            )

            # Barre de compte à rebours visuelle (active de 3s à 8s)
            timer_bar = (
                ColorClip(size=(880, 10), color=hex_to_rgb(style.color_box_correct), duration=5.0)
                .with_start(3.0)
                .with_position(("center", 530))
            )

            # D. Les 4 choix verticaux (A, B, C, D)
            choices_data = [
                ("A", q_item.choix_a),
                ("B", q_item.choix_b),
                ("C", q_item.choix_c),
                ("D", q_item.choix_d),
            ]

            card_layers: list[Any] = []
            y_start = 630
            card_h = 135
            gap = 25

            for k, (letter, choice_str) in enumerate(choices_data):
                y_pos = y_start + k * (card_h + gap)
                is_correct = (letter.upper() == q_item.reponse_correcte.strip().upper())

                # Étape 1 (3s à 8s) : Couleur par défaut
                box_default = (
                    ColorClip(
                        size=(920, card_h),
                        color=hex_to_rgb(style.color_box_default),
                        duration=5.0,
                    )
                    .with_start(3.0)
                    .with_position(("center", y_pos))
                )

                # Étape 2 (8s à 12s) : Bascule de couleur (vert si gagnant, gris si perdant)
                reveal_color = style.color_box_correct if is_correct else style.color_box_wrong
                box_revealed = (
                    ColorClip(
                        size=(920, card_h),
                        color=hex_to_rgb(reveal_color),
                        duration=4.0,
                    )
                    .with_start(8.0)
                    .with_position(("center", y_pos))
                )

                # Libellé du choix (3s à 12s)
                choice_label = f"{letter}.  {choice_str}"
                choice_text_clip = (
                    TextClip(
                        text=choice_label,
                        font=font_file,
                        font_size=36,
                        color="#FFFFFF",
                        size=(860, card_h - 15),
                        method="caption",
                        text_align="left",
                        duration=9.0,
                    )
                    .with_start(3.0)
                    .with_position(("center", y_pos + 8))
                )

                card_layers.extend([box_default, box_revealed, choice_text_clip])

            # Composition visuelle de la séquence de 12 secondes
            sequence_clip = CompositeVideoClip(
                [bg_seq, dark_overlay, badge_text, question_text_clip, timer_bar, *card_layers],
                size=(1080, 1920),
            ).with_duration(12.0)

            question_video_clips.append(sequence_clip)
            clips_to_close.append(sequence_clip)

            # E. Pistes audio synchronisées pour cette question
            # 1. Voix off TTS (0s)
            try:
                tts_audio = AudioFileClip(str(q_tts_path))
                clips_to_close.append(tts_audio)
                # Sécurité pour ne pas déborder sur la phase de révélation si l'énoncé est très long
                tts_dur = min(tts_audio.duration, 8.0)
                tts_cut = tts_audio.subclipped(0, tts_dur).with_start(sequence_start_time + 0.0)
                all_sfx_audio_clips.append(tts_cut)
            except Exception as e:
                logger.error(f"[{task_id}] Erreur chargement TTS question {q_idx + 1} : {e}")

            # 2. Voix off de la bonne réponse, au début de la révélation (8s)
            try:
                answer_audio = AudioFileClip(str(answer_tts_path))
                clips_to_close.append(answer_audio)
                answer_dur = min(answer_audio.duration, 4.0)
                answer_cut = answer_audio.subclipped(0, answer_dur).with_start(
                    sequence_start_time + 8.0
                )
                all_sfx_audio_clips.append(answer_cut)
            except Exception as e:
                logger.error(f"[{task_id}] Erreur chargement TTS réponse {q_idx + 1} : {e}")

            # 3. SFX Tick de compte à rebours (3s à 8s = durée 5s)
            if tick_path:
                try:
                    tick_audio = AudioFileClip(str(tick_path))
                    clips_to_close.append(tick_audio)
                    tick_dur = min(tick_audio.duration, 5.0)
                    tick_cut = tick_audio.subclipped(0, tick_dur).with_start(sequence_start_time + 3.0)
                    all_sfx_audio_clips.append(tick_cut)
                except Exception as e:
                    logger.error(f"[{task_id}] Erreur SFX tick : {e}")

            # 4. SFX Correct de confirmation (8s à 12s)
            if correct_path:
                try:
                    correct_audio = AudioFileClip(str(correct_path))
                    clips_to_close.append(correct_audio)
                    correct_dur = min(correct_audio.duration, 4.0)
                    correct_cut = correct_audio.subclipped(0, correct_dur).with_start(sequence_start_time + 8.0)
                    all_sfx_audio_clips.append(correct_cut)
                except Exception as e:
                    logger.error(f"[{task_id}] Erreur SFX correct : {e}")

        # 3. Concaténation des 5 séquences vidéo (5 x 12s = 60s)
        logger.info(f"[{task_id}] Concaténation des 5 clips vidéo...")
        final_video = concatenate_videoclips(question_video_clips, method="compose")
        clips_to_close.append(final_video)

        # 4. Mixage audio global
        audio_layers: list[Any] = []
        if music_path:
            try:
                bg_music = AudioFileClip(str(music_path))
                clips_to_close.append(bg_music)
                # Musique bouclée sur 60 secondes et atténuée à 18% du volume
                bg_music_looped = bg_music.with_effects(
                    [afx.AudioLoop(duration=60.0), afx.MultiplyVolume(0.18)]
                )
                audio_layers.append(bg_music_looped)
            except Exception as e:
                logger.warning(f"[{task_id}] Erreur musique de fond ({e}). Rendu sans musique.")

        # Ajout de toutes les voix et SFX superposés
        audio_layers.extend(all_sfx_audio_clips)

        if audio_layers:
            final_audio = CompositeAudioClip(audio_layers)
            clips_to_close.append(final_audio)
            final_video = final_video.with_audio(final_audio)

        # 5. Exportation vidéo optimisée pour Windows 10
        logger.info(f"[{task_id}] Début de l'encodage MP4 vers {output_filepath.name}...")
        final_video.write_videofile(
            str(output_filepath),
            fps=30,
            codec="libx264",
            audio_codec="aac",
            preset="fast",
            threads=4,
            logger=None,
        )
        logger.info(f"[{task_id}] Vidéo exportée avec succès : {output_filepath}")

    finally:
        # Libération rigoureuse des descripteurs de fichiers sous Windows 10
        for clip in clips_to_close:
            try:
                clip.close()
            except Exception:
                pass
        gc.collect()


# ---------------------------------------------------------------------------
# Gestionnaire de Tâche en Arrière-Plan
# ---------------------------------------------------------------------------
async def execute_quiz_background_task(task_id: str, payload: QuizPayload) -> None:
    """Tâche exécutée dans BackgroundTasks pour garantir une réponse HTTP immédiate."""
    output_filename = f"quiz_{task_id}.mp4"
    output_path = OUTPUT_DIR / output_filename
    temp_files: list[Path] = []

    try:
        TASK_REGISTRY[task_id]["status"] = "generating_tts"
        TASK_REGISTRY[task_id]["message"] = "Synthèse vocale des questions et réponses en cours (edge-tts)..."
        TASK_REGISTRY[task_id]["progress"] = 20

        # Étape 1 : Synthèse vocale asynchrone
        question_tts_paths, answer_tts_paths = await generate_tts_for_questions(
            task_id=task_id,
            questions=payload.questions,
            voice_name=payload.style.voice_name,
        )
        temp_files = question_tts_paths + answer_tts_paths

        TASK_REGISTRY[task_id]["status"] = "rendering_video"
        TASK_REGISTRY[task_id]["message"] = "Assemblage visuel MoviePy v2.x et encodage FFmpeg (preset fast, 4 threads)..."
        TASK_REGISTRY[task_id]["progress"] = 50

        # Étape 2 : Montage vidéo lourd exécuté dans un thread executor pour ne pas bloquer l'event loop
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            build_and_render_video,
            task_id,
            payload,
            question_tts_paths,
            answer_tts_paths,
            output_path,
        )

        # Vérification du fichier résultant
        if output_path.exists() and output_path.stat().st_size > 0:
            TASK_REGISTRY[task_id]["status"] = "completed"
            TASK_REGISTRY[task_id]["message"] = "Vidéo générée avec succès !"
            TASK_REGISTRY[task_id]["progress"] = 100
            TASK_REGISTRY[task_id]["output_file"] = f"output/{output_filename}"
            TASK_REGISTRY[task_id]["file_size_mb"] = round(output_path.stat().st_size / (1024 * 1024), 2)
            TASK_REGISTRY[task_id]["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        else:
            raise RuntimeError("Le fichier vidéo de sortie est manquant ou vide après l'export.")

    except Exception as exc:
        logger.exception(f"[{task_id}] Erreur critique lors de la génération du quiz : {exc}")
        TASK_REGISTRY[task_id]["status"] = "failed"
        TASK_REGISTRY[task_id]["message"] = f"Erreur lors du traitement : {str(exc)}"
        TASK_REGISTRY[task_id]["progress"] = 0

    finally:
        # Nettoyage automatique des fichiers temporaires dans temp/
        # Petit délai et garbage collect pour contourner les verrous de fichiers sous Windows
        gc.collect()
        time.sleep(0.2)
        for f in temp_files:
            try:
                if f.exists():
                    f.unlink()
                    logger.info(f"[{task_id}] Fichier temporaire nettoyé : {f.name}")
            except Exception as clean_err:
                logger.warning(f"[{task_id}] Impossible de supprimer {f} : {clean_err}")


# ---------------------------------------------------------------------------
# Application FastAPI & Endpoints
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Quiz Video Generator API",
    description="API locale d'automatisation de vidéos de quiz courtes verticales (60s, 9:16) avec MoviePy v2.x et edge-tts.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", tags=["Info"])
def api_root() -> dict[str, Any]:
    """Point d'entrée d'information sur l'API."""
    return {
        "service": "Quiz Video Generator API",
        "version": "1.0.0",
        "status": "online",
        "documentation": "/docs",
        "specs": {
            "format": "1080x1920 (9:16 vertical)",
            "duration": "60 seconds (5 questions x 12 seconds)",
            "engine": "MoviePy v2.x + FFmpeg + edge-tts",
        },
    }


@app.post(
    "/generate-quiz",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Générer une vidéo de quiz vertical de 60 secondes",
    tags=["Quiz Generator"],
)
async def generate_quiz(
    payload: QuizPayload,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """
    Endpoint principal : Reçoit le payload JSON contenant exactement 5 questions,
    valide les données, lance le rendu en tâche d'arrière-plan (`BackgroundTasks`)
    et retourne immédiatement un identifiant de tâche `task_id` sans bloquer le client.
    """
    task_id = str(uuid.uuid4())[:8]

    # Enregistrement initial de la tâche
    TASK_REGISTRY[task_id] = {
        "task_id": task_id,
        "status": "queued",
        "message": "Tâche enregistrée. Démarrage de la génération...",
        "progress": 5,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "output_file": f"output/quiz_{task_id}.mp4",
    }

    # Délégation au gestionnaire d'arrière-plan FastAPI
    background_tasks.add_task(execute_quiz_background_task, task_id, payload)

    return {
        "task_id": task_id,
        "status": "queued",
        "message": "Le rendu vidéo a été lancé en arrière-plan avec succès.",
        "status_url": f"/quiz-status/{task_id}",
        "download_url": f"/download/{task_id}",
        "estimated_duration": "60 secondes de vidéo (5 questions x 12 secondes)",
        "output_file": f"output/quiz_{task_id}.mp4",
    }


@app.get(
    "/quiz-status/{task_id}",
    summary="Vérifier l'état d'avancement de la génération d'une vidéo",
    tags=["Quiz Generator"],
)
def get_quiz_status(task_id: str) -> dict[str, Any]:
    """Retourne l'état courant de la tâche ('queued', 'generating_tts', 'rendering_video', 'completed', 'failed')."""
    task_info = TASK_REGISTRY.get(task_id)
    if not task_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aucune tâche trouvée avec l'ID '{task_id}'.",
        )
    return task_info


@app.get(
    "/download/{task_id}",
    summary="Télécharger le fichier MP4 final d'une tâche complétée",
    tags=["Quiz Generator"],
)
def download_quiz_video(task_id: str):
    """Télécharge directement la vidéo MP4 générée si le traitement est terminé."""
    task_info = TASK_REGISTRY.get(task_id)
    if not task_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Aucune tâche trouvée avec l'ID '{task_id}'.",
        )

    if task_info.get("status") != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La vidéo n'est pas encore prête. Statut actuel : {task_info.get('status')}.",
        )

    file_path = OUTPUT_DIR / f"quiz_{task_id}.mp4"
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Le fichier vidéo final est introuvable sur le disque.",
        )

    return FileResponse(
        path=str(file_path),
        media_type="video/mp4",
        filename=f"quiz_{task_id}.mp4",
    )


# ---------------------------------------------------------------------------
# Point d'Entrée Principal pour Exécution Directe
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    # Démarrage avec rechargement à chaud désactivé pendant le rendu vidéo pour éviter
    # tout redémarrage intempestif lors de modifications de fichiers temporaires
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
