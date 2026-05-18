#!/bin/bash
echo ""
echo " ================================================"
echo "   GeoQC Pro - Module 2"
echo " ================================================"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo " ERREUR: Python3 non trouvé."
    echo " Installe Python 3.10+ depuis https://python.org"
    exit 1
fi

# Install dependencies
echo " Vérification des dépendances..."
pip3 install -r requirements.txt --quiet --break-system-packages 2>/dev/null || \
pip3 install -r requirements.txt --quiet 2>/dev/null

# Create folders
mkdir -p uploads output historique

# Launch browser
echo ""
echo " Démarrage du serveur..."
echo " Ouvre http://localhost:5000 dans ton navigateur"
echo ""

# Open browser (Mac or Linux)
if [[ "$OSTYPE" == "darwin"* ]]; then
    sleep 1.5 && open http://localhost:5000 &
else
    sleep 1.5 && xdg-open http://localhost:5000 &
fi

python3 app.py
