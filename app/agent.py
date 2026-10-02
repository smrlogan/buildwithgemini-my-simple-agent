# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import datetime
import json
import os
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from dotenv import load_dotenv
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from app.a2ui_utils import a2ui_callback

load_dotenv()

FIRESTORE_PROJECT = "qwiklabs-gcp-03-92edf5af09d5"
BUCKET_NAME = "smart-pantry-recipes-qwiklabs-gcp-03-92edf5af09d5"

db = firestore.Client(project=FIRESTORE_PROJECT)
storage_client = storage.Client(project=FIRESTORE_PROJECT)


def search_recipes(query: str = "") -> str:
    """Searches the recipe database in Firestore for matching recipes.

    Args:
        query: Optional string to filter recipes by name, cuisine, or ingredient.

    Returns:
        JSON formatted string list of recipes.
    """
    docs = db.collection("recipes").stream()
    recipes = []
    query_lower = query.lower()
    for doc in docs:
        data = doc.to_dict()
        name_match = query_lower in data.get("name", "").lower()
        cuisine_match = query_lower in data.get("cuisine", "").lower()
        ing_match = any(query_lower in ing.lower() for ing in data.get("ingredients", []))
        if not query or name_match or cuisine_match or ing_match:
            recipes.append(data)
    return json.dumps(recipes)


def search_online_recipes(query: str) -> str:
    """Searches online recipe database (TheMealDB) for real recipes, instructions, and ingredients.

    Args:
        query: Name of the meal or ingredient to search online (e.g. 'chicken', 'pasta').

    Returns:
        JSON formatted list of matching online recipes.
    """
    api_key = os.environ.get("MEALDB_API_KEY", "1")
    url = f"https://www.themealdb.com/api/json/v1/{api_key}/search.php?s={urllib.parse.quote(query)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SmartPantryApp/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            meals = data.get("meals") or []
            results = []
            for meal in meals[:3]:
                ingredients = []
                for i in range(1, 21):
                    ing = meal.get(f"strIngredient{i}")
                    meas = meal.get(f"strMeasure{i}")
                    if ing and ing.strip():
                        ingredients.append(f"{meas.strip() if meas else ''} {ing.strip()}".strip())
                results.append({
                    "name": meal.get("strMeal"),
                    "category": meal.get("strCategory"),
                    "area": meal.get("strArea"),
                    "instructions": meal.get("strInstructions"),
                    "image_url": meal.get("strMealThumb"),
                    "ingredients": ingredients,
                })
            return json.dumps(results)
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch online recipes: {str(e)}"})


def get_pantry_inventory() -> str:
    """Retrieves all current pantry items from the Firestore database.

    Returns:
        JSON formatted string list of pantry items with quantity and category.
    """
    docs = db.collection("pantry").stream()
    items = [doc.to_dict() for doc in docs]
    return json.dumps(items)


def add_pantry_item(item: str, quantity: str, category: str = "General") -> str:
    """Adds a new item to the pantry collection in Firestore.

    Args:
        item: The name of the ingredient or item (e.g., 'Tomatoes').
        quantity: The amount or count (e.g., '2 lbs' or '1 can').
        category: The category (e.g., 'Produce', 'Dairy', 'Pantry').

    Returns:
        Confirmation message.
    """
    db.collection("pantry").add({
        "item": item,
        "quantity": quantity,
        "category": category,
        "expiration_days": 7,
    })
    return f"Successfully added {quantity} of {item} to your pantry inventory."


def generate_shopping_list(recipe_name: str) -> str:
    """Generates a shopping list of missing ingredients for a recipe by comparing with current pantry inventory.

    Args:
        recipe_name: The name of the recipe (e.g. 'Avocado Toast with Poached Egg').

    Returns:
        JSON formatted string of missing ingredients saved to shopping list.
    """
    docs = db.collection("recipes").stream()
    target_recipe = None
    for doc in docs:
        data = doc.to_dict()
        if recipe_name.lower() in data.get("name", "").lower():
            target_recipe = data
            break

    if not target_recipe:
        return json.dumps({"error": f"Recipe '{recipe_name}' not found."})

    pantry_docs = db.collection("pantry").stream()
    pantry_items = {doc.to_dict().get("item", "").lower() for doc in pantry_docs}

    required_ingredients = target_recipe.get("ingredients", [])
    missing = [
        ing for ing in required_ingredients
        if not any(p_item in ing.lower() or ing.lower() in p_item for p_item in pantry_items)
    ]

    for item in missing:
        db.collection("shopping_list").add({
            "item": item,
            "recipe": target_recipe.get("name"),
            "added_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        })

    return json.dumps({
        "recipe": target_recipe.get("name"),
        "missing_ingredients": missing,
        "message": f"Identified {len(missing)} missing ingredient(s) and saved to shopping_list collection.",
    })


def geocode_address(address: str) -> str:
    """Converts a street address or location name into geographic coordinates (latitude, longitude).

    Args:
        address: The address or place string to geocode (e.g., '1600 Amphitheatre Pkwy, Mountain View, CA').

    Returns:
        JSON formatted string containing name/address and location coordinates.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return json.dumps({"error": "GOOGLE_MAPS_API_KEY environment variable not set."})

    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={urllib.parse.quote(address)}&key={api_key}"
    try:
        req = urllib.request.urlopen(url)
        res = json.loads(req.read().decode())
        if res.get("status") == "OK" and res.get("results"):
            first = res["results"][0]
            loc = first["geometry"]["location"]
            return json.dumps({
                "address": first.get("formatted_address"),
                "location": {"latitude": loc["lat"], "longitude": loc["lng"]},
            })
        return json.dumps({"error": f"Geocoding failed with status: {res.get('status')}"})
    except Exception as e:
        return json.dumps({"error": f"Geocoding exception: {str(e)}"})


def find_nearby_places(latitude: float, longitude: float, place_type: str = "supermarket", radius_meters: float = 5000.0) -> str:
    """Finds nearby places of a specified type using Google Places API (New).

    Args:
        latitude: Latitude coordinate for search center.
        longitude: Longitude coordinate for search center.
        place_type: Type of place to search for (e.g. 'supermarket', 'grocery_store', 'restaurant').
        radius_meters: Search radius in meters (default 5000.0).

    Returns:
        JSON list of nearby places containing name, address, and location.
    """
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return json.dumps({"error": "GOOGLE_MAPS_API_KEY environment variable not set."})

    url = "https://places.googleapis.com/v1/places:searchNearby"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
    }
    body = json.dumps({
        "includedTypes": [place_type],
        "locationRestriction": {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": radius_meters,
            }
        },
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            places = data.get("places", [])
            results = []
            for p in places[:5]:
                results.append({
                    "name": p.get("displayName", {}).get("text"),
                    "address": p.get("formattedAddress"),
                    "location": p.get("location"),
                })
            return json.dumps(results)
    except Exception as e:
        return json.dumps({"error": f"Places API exception: {str(e)}"})


def generate_dish_image(dish_name: str, tool_context: ToolContext) -> str:
    """Generates an image of a dish or food item using gemini-3.1-flash-lite-image in global region,
    saves the image as an ADK artifact, uploads to public GCS bucket directly from memory bytes, and returns public URL.

    Args:
        dish_name: The name or description of the dish/item (e.g. 'Avocado Toast with Poached Egg').

    Returns:
        JSON formatted string containing public image_url and status message.
    """
    client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT, location="global")
    prompt = f"High quality food photograph of {dish_name}, appetizing presentation, professional lighting"

    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-image",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"]
            ),
        )

        image_bytes = None
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    image_bytes = part.inline_data.data
                    break

        if not image_bytes:
            return json.dumps({"error": f"Failed to generate image for '{dish_name}'."})

        clean_name = "".join(c for c in dish_name if c.isalnum() or c in (" ", "_")).strip().lower().replace(" ", "_")
        filename = f"{clean_name}_{int(time.time())}.jpg"

        # (1) Save artifact using tool_context for Playground
        artifact_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
        tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # (2) Upload directly from memory bytes to public Cloud Storage bucket
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type="image/jpeg")
        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

        return json.dumps({
            "dish_name": dish_name,
            "image_url": public_url,
            "status": "Successfully generated image, saved as artifact, and uploaded to public Cloud Storage.",
        })
    except Exception as e:
        return json.dumps({"error": f"Image generation failed: {str(e)}"})


def generate_dish_video(dish_name: str, tool_context: ToolContext) -> str:
    """Generates a short video for a dish or food item using gemini-omni-flash-preview in global region,
    saves the video as an ADK artifact, uploads to public GCS bucket directly from memory bytes, and returns public URL.

    Args:
        dish_name: The name or description of the dish/item (e.g. 'Avocado Toast with Poached Egg').

    Returns:
        JSON formatted string containing public video_url and status message.
    """
    client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT, location="global")
    prompt = f"Short 3-second food video presentation of {dish_name}, professional culinary lighting"

    try:
        interaction = client.interactions.create(
            model="gemini-omni-flash-preview",
            input=prompt,
        )

        video_bytes = None
        if hasattr(interaction, "output_video") and interaction.output_video:
            data = getattr(interaction.output_video, "data", None) or getattr(interaction.output_video, "bytes", None)
            if isinstance(data, str):
                video_bytes = base64.b64decode(data)
            elif isinstance(data, bytes):
                video_bytes = data
        
        if not video_bytes and hasattr(interaction, "outputs") and interaction.outputs:
            for out in interaction.outputs:
                data = getattr(out, "data", None) or getattr(out, "bytes", None)
                if data:
                    video_bytes = base64.b64decode(data) if isinstance(data, str) else data
                    break

        if not video_bytes:
            return json.dumps({"error": f"Failed to generate video for '{dish_name}'."})

        clean_name = "".join(c for c in dish_name if c.isalnum() or c in (" ", "_")).strip().lower().replace(" ", "_")
        filename = f"{clean_name}_{int(time.time())}.mp4"

        # (1) Save artifact using tool_context for Playground
        artifact_part = types.Part.from_bytes(data=video_bytes, mime_type="video/mp4")
        tool_context.save_artifact(filename=filename, artifact=artifact_part)

        # (2) Upload directly from memory bytes to public Cloud Storage bucket
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type="video/mp4")
        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"

        return json.dumps({
            "dish_name": dish_name,
            "video_url": public_url,
            "status": "Successfully generated video, saved as artifact, and uploaded to public Cloud Storage.",
        })
    except Exception as e:
        return json.dumps({"error": f"Video generation failed: {str(e)}"})


# Agent Engine Sandbox Code Executor & Memory Bank setup
METADATA_PATH = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
REASONING_ENGINE_ID = "projects/21887836476/locations/us-central1/reasoningEngines/6016192825899614208"
if os.path.exists(METADATA_PATH):
    try:
        with open(METADATA_PATH, "r") as f:
            meta = json.load(f)
            REASONING_ENGINE_ID = meta.get("remote_agent_runtime_id", REASONING_ENGINE_ID)
    except Exception:
        pass

MEMORY_BANK_ID = REASONING_ENGINE_ID.split("/")[-1]


async def generate_memories_callback(callback_context: CallbackContext):
    await callback_context.add_session_to_memory()
    return None


def memory_bank_service_builder():
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT,
        location="us-central1",
        agent_engine_id=MEMORY_BANK_ID,
    )


code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=REASONING_ENGINE_ID
)


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are a helpful AI Smart Pantry & Recipe Concierge. You help users track their pantry inventory, "
        "discover recipes, generate shopping lists, geocode locations, find nearby stores, generate food photos "
        "using gemini-3.1-flash-lite-image, and run Python code calculations in a secure Agent Engine sandbox environment. "
        "You remember user preferences, dietary restrictions, and food allergies across sessions. Pay critical attention "
        "to any user-stated allergies (e.g. peanuts, dairy, gluten, shellfish, eggs, tree nuts) and ensure that all suggested "
        "recipes, meal plans, pantry advice, and generated shopping lists strictly account for and avoid ingredients that "
        "trigger the user's allergies."
    ),
    workflow_description=(
        "Analyze the request and return structured UI when appropriate. "
        "Always use search_recipes for local recipes, search_online_recipes for web recipes, get_pantry_inventory / add_pantry_item "
        "for pantry, generate_shopping_list for shopping lists, geocode_address for coordinates, find_nearby_places for stores, "
        "generate_dish_image to create food images, and generate_dish_video to create short videos using gemini-omni-flash-preview. "
        "When executing Python code or mathematical calculations, write the code inside a ```python ... ``` markdown block so it runs in the Agent Engine sandbox."
    ),
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-2.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=a2ui_instruction,
    tools=[
        search_recipes,
        search_online_recipes,
        get_pantry_inventory,
        add_pantry_item,
        generate_shopping_list,
        geocode_address,
        find_nearby_places,
        generate_dish_image,
        generate_dish_video,
        PreloadMemoryTool(),
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
    code_executor=code_executor,
)

app = App(
    root_agent=root_agent,
    name="app",
)



