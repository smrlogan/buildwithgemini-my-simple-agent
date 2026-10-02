"""Seeding script for Firestore database."""

import google.auth
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-03-92edf5af09d5"

db = firestore.Client(project=PROJECT_ID, database="(default)")

# Seed recipes
recipes = [
    {
        "name": "Avocado Toast with Poached Egg",
        "cuisine": "Breakfast",
        "prep_time_minutes": 15,
        "ingredients": ["avocado", "bread", "egg", "salt", "pepper", "olive oil"],
        "instructions": "Toast bread, mash avocado with salt and pepper, top toast with avocado and poached egg.",
        "calories": 350,
    },
    {
        "name": "Garlic Butter Shrimp Pasta",
        "cuisine": "Italian",
        "prep_time_minutes": 25,
        "ingredients": ["shrimp", "pasta", "garlic", "butter", "lemon", "parsley"],
        "instructions": "Cook pasta. Sauté garlic and shrimp in butter, toss with pasta, lemon juice, and fresh parsley.",
        "calories": 520,
    },
    {
        "name": "Mediterranean Chickpea Salad",
        "cuisine": "Mediterranean",
        "prep_time_minutes": 10,
        "ingredients": ["chickpeas", "cucumber", "cherry tomatoes", "feta cheese", "olive oil", "lemon"],
        "instructions": "Combine drained chickpeas, diced cucumber, tomatoes, and feta. Toss with olive oil and lemon juice.",
        "calories": 310,
    },
]

# Seed pantry items
pantry_items = [
    {"item": "Avocado", "quantity": "3 whole", "category": "Produce", "expiration_days": 4},
    {"item": "Eggs", "quantity": "12 dozen", "category": "Dairy & Eggs", "expiration_days": 14},
    {"item": "Sourdough Bread", "quantity": "1 loaf", "category": "Bakery", "expiration_days": 5},
    {"item": "Pasta", "quantity": "500g", "category": "Pantry", "expiration_days": 180},
    {"item": "Olive Oil", "quantity": "1 bottle", "category": "Pantry", "expiration_days": 365},
]


def main():
    print(f"Seeding Firestore in project: {PROJECT_ID}...")

    # Clear existing test data if any
    for doc in db.collection("recipes").stream():
        doc.reference.delete()
    for doc in db.collection("pantry").stream():
        doc.reference.delete()

    # Add recipes
    recipes_ref = db.collection("recipes")
    for r in recipes:
        recipes_ref.add(r)
        print(f"Added recipe: {r['name']}")

    # Add pantry items
    pantry_ref = db.collection("pantry")
    for p in pantry_items:
        pantry_ref.add(p)
        print(f"Added pantry item: {p['item']}")

    print("Firestore seeding completed successfully!")


if __name__ == "__main__":
    main()
