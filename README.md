# 📬 Recos du jour — Discord + Gemini + GitHub Actions

Chaque jour, le script demande à Gemini **9 recommandations pour 7 catégories**
(jeux vidéo, films, manga, animé, livres, K-Drama, séries) et les poste sur Discord,
**un salon par catégorie**. Un fichier `history.json` évite les doublons.

## Les 9 emplacements (par catégorie)

| # | Emplacement | Contrainte de genre ? |
|---|---|---|
| 1 | 🔍 Mystère | oui |
| 2 | 💕 Romance | oui |
| 3 | 🌱 Développement personnel | oui |
| 4 | 🧠 Psychologique | oui |
| 5 | 🌅 Espoir & inspiration | oui |
| 6 | ☕ Cozy & réconfort | oui |
| 7 | 🔥 Populaire · Incontournable | non |
| 8 | 💎 Niche & Culte | non |
| 9 | 🎲 Wildcard · Curiosité | non |

Pour passer à 8, supprime une entrée de la liste `SLOTS` dans `main.py`.
Pour changer un genre, modifie son champ `brief`.

## Installation (≈ 30 min)

### 1. Discord : 7 salons + 7 webhooks
1. Crée un serveur (ou utilise le tien) avec 7 salons : `#jeux`, `#films`, `#manga`, `#anime`, `#livres`, `#kdrama`, `#series`.
2. Pour chaque salon : **Paramètres du salon → Intégrations → Webhooks → Nouveau webhook → Copier l'URL**.

### 2. Clé API Gemini
Crée une clé sur <https://aistudio.google.com/apikey>.

### 3. Dépôt GitHub
1. Crée un dépôt (privé conseillé) et pousse ces fichiers :
   `main.py`, `history.json`, `.gitignore`, `README.md`, `.github/workflows/daily.yml`.
2. **Settings → Secrets and variables → Actions → New repository secret**, ajoute :

| Secret | Valeur |
|---|---|
| `GEMINI_API_KEY` | ta clé Gemini |
| `DISCORD_WEBHOOK_JEUX` | URL du webhook de #jeux |
| `DISCORD_WEBHOOK_FILMS` | URL du webhook de #films |
| `DISCORD_WEBHOOK_MANGA` | URL du webhook de #manga |
| `DISCORD_WEBHOOK_ANIME` | URL du webhook de #anime |
| `DISCORD_WEBHOOK_LIVRES` | URL du webhook de #livres |
| `DISCORD_WEBHOOK_KDRAMA` | URL du webhook de #kdrama |
| `DISCORD_WEBHOOK_SERIES` | URL du webhook de #series |

3. *(Optionnel)* Dans l'onglet **Variables**, crée `GEMINI_MODEL` pour changer de modèle
   (défaut : `gemini-2.5-flash`). Si Google a retiré ce modèle, mets-en un plus récent.
4. **Settings → Actions → General → Workflow permissions** : choisis *Read and write permissions*.

### 4. Premier test
- Onglet **Actions → Recommandations quotidiennes → Run workflow**.
- Premier essai : coche `dry_run` et mets `only` = `jeux` → les résultats s'affichent dans les logs, rien n'est envoyé.
- Puis relance sans `dry_run` pour voir le vrai message dans Discord.

## Test en local

```bash
export GEMINI_API_KEY="..."
DRY_RUN=true ONLY=jeux python main.py        # affiche seulement
export DISCORD_WEBHOOK_JEUX="https://discord.com/api/webhooks/..."
ONLY=jeux python main.py                      # envoie vraiment
```

Aucune dépendance à installer (bibliothèque standard uniquement). Python 3.9+.

## Comment ça marche

1. Le workflow se lance chaque jour à 06:00 UTC (modifiable dans `daily.yml`, ligne `cron`).
2. Pour chaque catégorie : un appel Gemini avec une sortie JSON structurée, qui reçoit les 300 derniers titres déjà suggérés à éviter.
3. Le code vérifie aussi les doublons sur **tout** l'historique (titres normalisés : accents, majuscules, « The/Le/La »). Les emplacements invalides ou en doublon sont redemandés une fois.
4. Envoi Discord (embeds colorés, découpés si besoin pour respecter les limites).
5. `history.json` est mis à jour et committé par le workflow, ce qui garde aussi le dépôt « actif » (GitHub désactive les crons après 60 jours sans activité).

## Limites à connaître

- **Hallucinations** : l'IA peut inventer ou confondre une œuvre. L'année et le créateur affichés servent à vérifier vite.
- **Palier gratuit Gemini** : limité en requêtes par minute/jour ; le script fait 7 appels par jour avec 8 s de pause, et réessaie en cas d'erreur 429/503.
- **Cron GitHub** : peut démarrer avec quelques minutes (parfois plus) de retard.
- Si une catégorie échoue, les autres sont quand même envoyées et le job est marqué en échec pour te prévenir.
