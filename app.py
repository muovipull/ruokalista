import os
import csv
import json
import re
from datetime import datetime, timedelta
import requests
from flask import Flask, Response, jsonify, send_from_directory, request
from flask_sqlalchemy import SQLAlchemy
from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__, static_folder='.')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(BASE_DIR, 'database.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class DishRating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    dish_key = db.Column(db.String(150), index=True, nullable=False)
    maku = db.Column(db.Integer, nullable=False)
    rakenne = db.Column(db.Integer, nullable=False)
    ulkonako = db.Column(db.Integer, nullable=False)
    avg = db.Column(db.Float, nullable=False)

with app.app_context():
    db.create_all()

URL_MEALS = "https://aromimenu.cgisaas.fi/ISSAromieMenus/FI/Default/iss/HKIViikinNormaalikoulu/api/Common/Restaurant/RestaurantMeals"
URL_NUTRIENTS = "https://aromimenu.cgisaas.fi/ISSAromieMenus/FI/Default/iss/HKIViikinNormaalikoulu/api/Common/Restaurant/GetRestaurentMealNutrients"
MENU_CSV_PATH = os.path.join(BASE_DIR, "viikon_ruokalista_cache.csv")

API_PAYLOAD = {
    "Id": "2d1d4df3-5ab7-46e2-b634-87446c067a6f",
    "DinerGroupId": "4b2787ff-dc7c-4186-9eeb-36ba9574bf96",
    "NutrientGroupId": "0ea0ee0d-22c9-4e49-8a7b-8de201ddaf84",
    "DietGroupId": "63062e9f-93a0-41d4-8e87-260d3247c5f6",
    "FilterDietGroupId": "63062e9f-93a0-41d4-8e87-260d3247c5f6",
    "DietId": None,
    "ConceptId": None,
    "Name": "HY Alakoulu",
    "Code": "HY Alakoulu",
    "RestaurantId": "08201621-be48-4c4c-a3d1-df7ef9a58d1d",
    "UniqueCode": "HY Alakoulu",
    "IndexNumber": 83,
    "NameOrCode": "HY Alakoulu",
    "WeekDays": ["1", "2", "3", "4", "5"],
    "WeekDay0": False,
    "WeekDay1": True,
    "WeekDay2": True,
    "WeekDay3": True,
    "WeekDay4": True,
    "WeekDay5": True,
    "WeekDay6": False,
    "DietType": None,
    "IsActiveSuitability": False,
    "SuitabilityDietIds": [],
}

API_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}

# ---------------------------------------------------------------------------
# API-REITIT ARVOSTELUILLE JA MENUILLE (CSV-PYSYVÄISUUDELLA)
# ---------------------------------------------------------------------------

@app.route('/api/ratings', methods=['GET'])
def get_all_ratings():
    ratings = DishRating.query.all()
    grouped = {}
    for r in ratings:
        if r.dish_key not in grouped:
            grouped[r.dish_key] = []
        grouped[r.dish_key].append({"maku": r.maku, "rakenne": r.rakenne, "ulkonako": r.ulkonako})
    return jsonify(grouped)

@app.route('/api/ratings', methods=['POST'])
def post_rating():
    data = request.json
    dish_key = data.get('dish_key', '').lower().strip()
    maku = int(data.get('maku', 3))
    rakenne = int(data.get('rakenne', 3))
    ulkonako = int(data.get('ulkonako', 3))
    
    if not dish_key:
        return jsonify({"error": "Virheellinen ruoan nimi"}), 400

    avg = round((maku + rakenne + ulkonako) / 3, 1)
    new_rating = DishRating(dish_key=dish_key, maku=maku, rakenne=rakenne, ulkonako=ulkonako, avg=avg)
    db.session.add(new_rating)
    db.session.commit()

    return jsonify({"success": True, "avg": avg})

@app.route('/api/weekly-menu', methods=['GET'])
def get_weekly_menu_api():
    """Lukee ruokalistan CSV-tiedostosta. Jos tiedostoa ei ole, hakee API:sta ja tallentaa CSV:hen."""
    
    # 1. Jos CSV-tiedosto on olemassa, ladataan tiedot suoraan sieltä!
    if os.path.exists(MENU_CSV_PATH):
        try:
            structured_menu = []
            with open(MENU_CSV_PATH, mode='r', encoding='utf-8-sig') as f:
                reader = csv.reader(f, delimiter=';')
                header = next(reader, None) # Ohitetaan otsikkorivi
                for row in reader:
                    if len(row) >= 4:
                        menu_date, date_str, meal_name, json_data_str = row[0], row[1], row[2], row[3]
                        meals_data = json.loads(json_data_str)
                        
                        # Ryhmitellään päivämäärän mukaan
                        existing_day = next((d for d in structured_menu if d["date"] == date_str), None)
                        if existing_day:
                            existing_day["meals"].append({"meal_name": meal_name, "dishes": meals_data})
                        else:
                            structured_menu.append({
                                "menu_date": menu_date,
                                "date": date_str,
                                "meals": [{"meal_name": meal_name, "dishes": meals_data}]
                            })
            return jsonify(structured_menu)
        except Exception as e:
            app.logger.error(f"Virhe CSV-luennassa: {e}")

    # 2. Jos tiedostoa ei ole, haetaan tiedot Aromimenun API:sta
    today = datetime.now()
    start_of_week = today - timedelta(days=today.weekday())
    end_of_week = start_of_week + timedelta(days=4)

    params_meals = {
        "Id": "08201621-be48-4c4c-a3d1-df7ef9a58d1d",
        "StartDate": start_of_week.strftime("%Y-%m-%dT00:00:00.000Z"),
        "EndDate": end_of_week.strftime("%Y-%m-%dT23:59:59.000Z"),
    }

    try:
        response = requests.post(URL_MEALS, params=params_meals, json=API_PAYLOAD, headers=API_HEADERS, timeout=10)
        if response.status_code != 200:
            return jsonify({"error": "Virhe ruokalistan haussa"}), response.status_code

        days_data = response.json()
        structured_menu = []
        csv_rows = []
        pork_keywords = ["possu", "porsas", "sika", "kinkku", "pekoni"]

        for day in days_data:
            menu_date = day.get("MenuDate", "Tuntematon pvm")
            raw_date = day.get("Date", "").split("T")[0]
            day_entry = {"menu_date": menu_date, "date": raw_date, "meals": []}

            meals = day.get("Meals", [])
            for meal in meals:
                meal_name = meal.get("MealName", "Lounas")
                meal_id = meal.get("MealId")

                dt_obj = datetime.strptime(raw_date, "%Y-%m-%d") - timedelta(days=1)
                date_offset = dt_obj.strftime("%Y-%m-%dT22:00:00.000Z")

                params_nutrients = {
                    "Id": "08201621-be48-4c4c-a3d1-df7ef9a58d1d",
                    "StartDateOffset": date_offset,
                    "EndDateOffset": date_offset,
                    "orgCultureId": "4286d1a6-3f4d-469a-8219-a72d3e84b9f1",
                    "showNutritional": "true",
                    "mealId": meal_id,
                }

                dishes_list = []
                try:
                    nutr_res = requests.post(URL_NUTRIENTS, params=params_nutrients, json=API_PAYLOAD, headers=API_HEADERS, timeout=5)
                    if nutr_res.status_code == 200:
                        nutr_data = nutr_res.json()
                        if nutr_data:
                            for item in nutr_data:
                                dish_title = item.get("Dish", "N/A")
                                ingredients = item.get("IngredientName", "Ei ainesosia saatavilla")
                                contains_pork = any(kw in ingredients.lower() for kw in pork_keywords)

                                nutrients = item.get("Nutrients", [])
                                nutrient_summary = []
                                for n in nutrients[:4]:
                                    name = n.get("NutrientWithPad", "").replace("<span>", "").replace("</span>", "")
                                    val = n.get("NutrientWithUnit", "")
                                    nutrient_summary.append({"name": name, "value": val})

                                dishes_list.append({
                                    "dish": dish_title,
                                    "ingredients": ingredients,
                                    "contains_pork": contains_pork,
                                    "nutrients": nutrient_summary
                                })
                except Exception:
                    pass

                day_entry["meals"].append({
                    "meal_name": meal_name,
                    "dishes": dishes_list
                })

                # Tallennetaan rivi CSV-taulukkoon (muunnetaan listat JSON-merkkijonoksi)
                csv_rows.append([menu_date, raw_date, meal_name, json.dumps(dishes_list, ensure_ascii=False)])

            structured_menu.append(day_entry)

        # Kirjoitetaan tiedot pysyvästi CSV-tiedostoon
        with open(MENU_CSV_PATH, mode='w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f, delimiter=';')
            writer.writerow(["MenuDate", "Date", "MealName", "DishesJson"])
            writer.writerows(csv_rows)

        return jsonify(structured_menu)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/watch/today', methods=['GET'])
def get_watch_today():
    """Palauttaa vain tämän päivän ruoat ja possutiedot kellonäyttöä varten."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # Tarkistetaan löytyykö CSV-välimuisti
    if not os.path.exists(MENU_CSV_PATH):
        return jsonify({"error": "Ei ruokalistaa saatavilla"}), 404

    try:
        today_meals = []
        has_pork = False

        with open(MENU_CSV_PATH, mode='r', encoding='utf-8-sig') as f:
            reader = csv.reader(f, delimiter=';')
            next(reader, None) # Ohitetaan otsikko
            for row in reader:
                if len(row) >= 4 and row[1] == today_str:
                    meal_name, dishes_json = row[2], row[3]
                    dishes = json.loads(dishes_json)
                    
                    meal_dishes = []
                    for d in dishes:
                        meal_dishes.append(d["dish"])
                        if d["contains_pork"]:
                            has_pork = True
                    
                    today_meals.append({
                        "meal_name": meal_name,
                        "dishes": meal_dishes
                    })

        if not today_meals:
            # Jos tälle päivälle ei löydy suoraan (esim. viikonloppu), näytetään joku tai ilmoitus
            return jsonify({
                "date": today_str,
                "message": "Ei ruokailua tänään.",
                "has_pork": False,
                "meals": []
            })

        return jsonify({
            "date": today_str,
            "has_pork": has_pork,
            "meals": today_meals
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/watch')
def serve_watch():
    return send_from_directory('.', 'watch.html')

@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)