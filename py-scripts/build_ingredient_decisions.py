#!/usr/bin/env python3
"""Build ingredient-decisions.json for resolve_ingredient_review.py --batch.

Combines:
* curated ``by_name`` rules (split/ignore for the highest-frequency names),
* auto-generated ``by_entry`` renames for prep-artifact rows ("peeled and
  diced pears" -> "pear"), derived from the current review queue.

Run: python3 py-scripts/build_ingredient_decisions.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate_catalog as gc  # noqa: E402
from resolve_ingredient_review import load_json, order_review_entries  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DECISIONS_PATH = REPO_ROOT / "ingredient-decisions.json"

# Curated whole-name rules for the highest-frequency ambiguous connectors.
BY_NAME: dict[str, dict] = {
    # real compounds -> split
    "frozen peas and carrot": {"action": "split", "names": ["frozen peas", "frozen carrots"]},
    "red or yellow bell pepper": {"action": "split", "names": ["red bell pepper", "yellow bell pepper"]},
    "red or green bell pepper": {"action": "split", "names": ["red bell pepper", "green bell pepper"]},
    "yellow or orange bell pepper": {"action": "split", "names": ["yellow bell pepper", "orange bell pepper"]},
    "cherry or grape tomato": {"action": "split", "names": ["cherry tomato", "grape tomato"]},
    "cubed cooked chicken or turkey": {"action": "split", "names": ["cubed cooked chicken", "cubed cooked turkey"]},
    "lean ground beef or turkey": {"action": "split", "names": ["lean ground beef", "lean ground turkey"]},
    "chopped fresh basil or parsley": {"action": "split", "names": ["chopped fresh basil", "chopped fresh parsley"]},
    "chicken legs and thigh": {"action": "split", "names": ["chicken leg", "chicken thigh"]},
    "white or yellow onion": {"action": "split", "names": ["white onion", "yellow onion"]},
    "shredded iceberg or romaine lettuce": {
        "action": "split",
        "names": ["shredded iceberg lettuce", "shredded romaine lettuce"],
    },
    "salt and freshly cracked black pepper": {"action": "split", "names": ["salt", "freshly cracked black pepper"]},
    "chopped cilantro and": {"action": "split", "names": ["chopped cilantro", "sliced green onions"]},
    "yellow or white onion": {"action": "split", "names": ["yellow onion", "white onion"]},
    "grape or cherry tomato": {"action": "split", "names": ["grape tomato", "cherry tomato"]},
    "green onion and cilantro": {"action": "split", "names": ["green onion", "cilantro"]},
    "sesame seeds and": {"action": "split", "names": ["sesame seeds", "sliced green onions"]},
    "chopped fresh cilantro and": {"action": "split", "names": ["chopped fresh cilantro", "sliced green onions"]},
    "grated parmesan cheese and": {"action": "split", "names": ["grated parmesan cheese", "chopped parsley"]},
    # products / choices -> keep as one ingredient, stop flagging
    "butter or margarine": {"action": "ignore"},
    "margarine or butter": {"action": "ignore"},
    "half and half": {"action": "ignore"},
    "sweet and sour sauce": {"action": "ignore"},
    "cook and serve butterscotch pudding mix": {"action": "ignore"},
    "knorr crab and corn soup": {"action": "ignore"},
    "chicken broth or stock": {"action": "ignore"},
    "chicken stock or broth": {"action": "ignore"},
    "low-sodium chicken broth or stock": {"action": "ignore"},
    "chicken or vegetable broth": {"action": "ignore"},
    "chicken broth or vegetable broth": {"action": "ignore"},
    "beef broth or water": {"action": "ignore"},
    "castor sugar or superfine sugar": {"action": "ignore"},
    "sriracha or other hot sauce": {"action": "ignore"},
    "garlic and herb cheese spread such as boursin": {"action": "ignore"},
    "frozen bell pepper and onion blend": {"action": "ignore"},
    "pork and bean": {"action": "ignore"},
    "tomatoes with garlic and onion": {"action": "ignore"},
    "tomato and clam juice cocktail such as clamato": {"action": "ignore"},
    "tomato and clam juice cocktail": {"action": "ignore"},
    "vanilla bean paste or extract": {"action": "ignore"},
    "mushroom stems and piece": {"action": "ignore"},
    "frozen mixed peas and carrot": {"action": "ignore"},
    "bread flour or all-purpose flour": {"action": "ignore"},
    "olive oil or butter": {"action": "ignore"},
    "butter or olive oil": {"action": "ignore"},
    "agave syrup or honey": {"action": "ignore"},
    "avocado or canola oil": {"action": "ignore"},
    "canola or vegetable oil": {"action": "ignore"},
    "canola or peanut oil": {"action": "ignore"},
    "less-sodium tamari or soy sauce": {"action": "ignore"},
    "dijon or yellow mustard": {"action": "ignore"},
    "refrigerated or fresh cheese tortellini": {"action": "ignore"},
    "creme fraiche or heavy cream": {"action": "ignore"},
    "melted butter or margarine": {"action": "ignore"},
    "refrigerated spinach and mozzarella ravioli": {"action": "ignore"},
    "red and green candied cherry": {"action": "ignore"},
    "herb and garlic feta": {"action": "ignore"},
    "onion soup and dip mix": {"action": "ignore"},
    "quaker oats quick or old fashioned": {"action": "ignore"},
    "classico tomato and basil sauce": {"action": "ignore"},
    "blueberries or frozen blueberry": {"action": "ignore"},
    "black and white sesame seeds": {"action": "ignore"},
    "chive and onion cream cheese": {"action": "ignore"},
    "liquid shrimp and crab boil seasoning": {"action": "ignore"},
    "campbells condensed cream of chicken soup regular or 98 fat free": {"action": "ignore"},
    "breakstones or knudsen sour cream": {"action": "ignore"},
    "chopped fresh oregano or 1 teaspoon dried oregano": {"action": "ignore"},
    "minced garlic or garlic paste": {"action": "ignore"},
    "packed fresh cilantro leaves and tender stem": {"action": "ignore"},
    "cilantro leaves and stem": {"action": "ignore"},
    "chinese black vinegar or balsamic": {"action": "ignore"},
    "creamy or chunky peanut butter": {"action": "ignore"},
    "whole milk or half-and-half": {"action": "ignore"},
    "karo light or dark corn syrup": {"action": "ignore"},
    "vegetable or chicken broth": {"action": "ignore"},
    "vegetable broth or chicken broth": {"action": "ignore"},
    "milk or water": {"action": "ignore"},
    "gin or vodka": {"action": "ignore"},
    "tomatoes and green chile": {"action": "ignore"},
    "gochujang korean hot and sweet pepper paste": {"action": "ignore"},
    "knorr cream of chicken and corn": {"action": "ignore"},
    "knorr real chinese soup crab and corn": {"action": "ignore"},
    "macaroni and cheese dinner mix such as kraft": {"action": "ignore"},
    "macaroni and cheese mix such as kraft macaroni cheese dinner": {"action": "ignore"},
    "box macaroni and cheese mix": {"action": "ignore"},
    "box white or vanilla cake mix": {"action": "ignore"},
    "box white or yellow cake mix": {"action": "ignore"},
    "layer size box yellow or white cake mix": {"action": "ignore"},
    "sweet and spicy seafood seasoning such as mccormick": {"action": "ignore"},
    "tomatoes with lime juice and cilantro such as rotel": {"action": "ignore"},
    "breakstones reduced fat or knudsen light sour cream": {"action": "ignore"},
    "ground black and red pepper blend such as mccormick hot shot": {"action": "ignore"},
    "browning and seasoning sauce": {"action": "ignore"},
    "chicken flavor rice and pasta blend": {"action": "ignore"},
    "chocolate instant pudding and pie fillingmix": {"action": "ignore"},
    "cookies and cream instant pudding mix such as jell-o oreo": {"action": "ignore"},
    "instant cookies and creme or vanilla pudding mix": {"action": "ignore"},
    "golden onion soup and dip mix": {"action": "ignore"},
    "jug shake and pour pancake mix": {"action": "ignore"},
    "arugula and spinach salad mix": {"action": "ignore"},
    "lean ground beef and pork mix": {"action": "ignore"},
    "reduced-fat and reduced-sodium condensed cream of mushroom soup": {"action": "ignore"},
    "reduced-sodium soy sauce or tamari": {"action": "ignore"},
    "reduced-sodium tamari or low-sodium soy sauce": {"action": "ignore"},
    "frozen carrots and green pea": {"action": "ignore"},
    "frozen yellow and white whole kernel corn": {"action": "ignore"},
    "frozen whole-kernel corn with red and green bell pepper": {"action": "ignore"},
    "frozen cauliflower with broccoli and red pepper": {"action": "ignore"},
    "frozen southwestern-style corn and pepper": {"action": "ignore"},
    "frozen raspberries and blueberry": {"action": "ignore"},
    "frozen potato and cheese filled pierogy": {"action": "ignore"},
    "frozen cheese and potato pierogies such as mrs ts 4 cheese medley": {"action": "ignore"},
    "frozen fully cooked beef and pork meatball": {"action": "ignore"},
    "frozen pre-cooked pork or turkey sausage patty": {"action": "ignore"},
    "frozen hash brown potatoes with peppers and onion such as ore-ida potatoes obrien": {"action": "ignore"},
    "frozen dumplings or potsticker": {"action": "ignore"},
    "chitterlings or frozen chitterling": {"action": "ignore"},
    "leftover green bean casserole or thawed frozen mixed vegetable": {"action": "ignore"},
    "jarred german wiener or vienna sausage": {"action": "ignore"},
    "cantanzaro herbs or italian seasoning": {"action": "ignore"},
    "cantanzaro herbs such as savory spice shop or italian seasoning": {"action": "ignore"},
    "dried oregano or italian seasoning": {"action": "ignore"},
    "herbes de provence or italian seasoning": {"action": "ignore"},
    "chili-lime seasoning or chili powder": {"action": "ignore"},
    "firmly packed baby spinach or spring mix": {"action": "ignore"},
    "chopped fresh cilantro or": {"action": "ignore"},
    "skinless chicken breasts grilled and": {
        "action": "rename",
        "name": "skinless chicken breast",
        "preparation": "grilled and sliced",
    },
    "grated parmesan cheese or": {"action": "rename", "name": "grated parmesan cheese", "preparation": "to taste"},
    "grated parmesan cheese 3 ounces or": {
        "action": "rename",
        "name": "grated parmesan cheese",
        "preparation": "3 ounces, to taste",
    },
    "avocado or olive oil": {"action": "ignore"},
    "grapeseed oil or olive oil": {"action": "ignore"},
    "mexican crema or sour cream": {"action": "ignore"},
    "macaroni and cheese mix": {"action": "ignore"},
    "coarse sea salt or kosher salt": {"action": "ignore"},
    "vanilla bean paste or vanilla extract": {"action": "ignore"},
    "dry onion and mushroom soup mix": {"action": "ignore"},
    "raisins or dried currant": {"action": "ignore"},
    "pineapple and orange juice blend": {"action": "ignore"},
    "sweet and sour mix": {"action": "ignore"},
    "olive oil or melted butter": {"action": "ignore"},
    "jam or jelly of choice": {"action": "ignore"},
    "almond breeze original almondmilk or almond breeze vanilla almondmilk": {"action": "ignore"},
    "frozen mixed vegetables or frozen peas and carrot": {"action": "ignore"},
    "peas and carrot": {"action": "ignore"},
    "jarred caramel sauce or dulce de leche": {"action": "ignore"},
    "garlic and herb seasoning blend": {"action": "ignore"},
    "tomato sauce or puree": {"action": "ignore"},
    "kosher salt or": {"action": "rename", "name": "kosher salt", "preparation": "to taste"},
    "taste salt and pepper": {"action": "rename", "name": "salt and pepper"},
    "rinsed and drained canned black bean": {
        "action": "rename",
        "name": "canned black bean",
        "preparation": "rinsed and drained",
    },
    "cabernet sauvignon or other dry red wine": {"action": "rename", "name": "dry red wine"},
    "boneless and skinless chicken breast": {"action": "rename", "name": "boneless skinless chicken breast"},
    "salt or": {"action": "rename", "name": "salt", "preparation": "to taste"},
    "sriracha or": {"action": "rename", "name": "sriracha", "preparation": "to taste"},
    "red pepper flakes or": {"action": "rename", "name": "red pepper flakes", "preparation": "to taste"},
    "jalapeno chile or more": {"action": "rename", "name": "jalapeno chile", "preparation": "or more to taste"},
    "leeks white and pale green parts only": {
        "action": "rename",
        "name": "leek",
        "preparation": "white and pale green parts only",
    },
    "hot water or as needed": {"action": "rename", "name": "hot water", "preparation": "as needed"},
    "sweet and sour sauce for dipping": {"action": "rename", "name": "sweet and sour sauce", "preparation": "for dipping"},
    "packed cup light or dark brown sugar": {"action": "rename", "name": "brown sugar", "preparation": "packed"},
    # alias/optional residuals from the pathological source lines
    "cup plus 2 tablespoons creamy peanut butter divided": {"action": "rename", "name": "creamy peanut butter"},
    "cup plus 3 tablespoons irish cream liqueur divided": {"action": "rename", "name": "irish cream liqueur"},
    "tablespoon plus 2 1/2 teaspoon garam masala divided": {"action": "rename", "name": "garam masala"},
    "cup plus 1 tablespoon sweet vermouth divided": {"action": "rename", "name": "sweet vermouth"},
}

# Long-tail rules from the final queue pass: real "X and Y" compounds,
# product names, broken rows with embedded junk, and "X or Y" choices
# with amounts that the class rules cannot match.
LONG_TAIL_RULES: dict[str, dict] = {
    # real compounds -> split
    "ketchup and yellow mustard": {"action": "split", "names": ["ketchup", "yellow mustard"]},
    "cinnamon and nutmeg": {"action": "split", "names": ["cinnamon", "nutmeg"]},
    "cinnamon and/or nutmeg": {"action": "split", "names": ["cinnamon", "nutmeg"]},
    "ground black pepper and salt": {"action": "split", "names": ["ground black pepper", "salt"]},
    "chicken drumsticks and thigh": {"action": "split", "names": ["chicken drumstick", "chicken thigh"]},
    "chicken wings and drummette": {"action": "split", "names": ["chicken wing", "chicken drummette"]},
    "graham cracker crumbs and sweetened condensed milk": {
        "action": "split",
        "names": ["graham cracker crumbs", "sweetened condensed milk"],
    },
    "crackers and crostini": {"action": "split", "names": ["crackers", "crostini"]},
    "spinach and arugula": {"action": "split", "names": ["spinach", "arugula"]},
    "white and crimini mushroom": {"action": "split", "names": ["white mushroom", "crimini mushroom"]},
    "softened butter and flour": {"action": "split", "names": ["softened butter", "flour"]},
    "sriracha sauce and soy sauce": {"action": "split", "names": ["sriracha sauce", "soy sauce"]},
    "chopped fresh chives and paprika": {"action": "split", "names": ["chopped fresh chives", "paprika"]},
    "tartar sauce and lemon wedge": {"action": "split", "names": ["tartar sauce", "lemon wedge"]},
    "lime wedge and thai basil sprig": {"action": "split", "names": ["lime wedge", "thai basil sprig"]},
    "lime wedges and lime crema": {"action": "split", "names": ["lime wedges", "lime crema"]},
    "whipped cream and melted chocolate": {"action": "split", "names": ["whipped cream", "melted chocolate"]},
    "pico de gallo and hot taco sauce such as taco bell": {
        "action": "split",
        "names": ["pico de gallo", "hot taco sauce"],
    },
    "pickled jalapeno peppers and juice": {
        "action": "split",
        "names": ["pickled jalapeno peppers", "pickled jalapeno pepper juice"],
    },
    "each of salt and black pepper": {"action": "split", "names": ["salt", "black pepper"]},
    "each paprika and salt": {"action": "split", "names": ["paprika", "salt"]},
    "of salt and pepper": {"action": "split", "names": ["salt", "pepper"]},
    "pomegranate arils and a sprig of rosemary": {
        "action": "split",
        "names": ["pomegranate arils", "rosemary sprig"],
    },
    "mini oreos and extra crushed oreo": {"action": "split", "names": ["mini oreos", "extra crushed oreos"]},
    "peanuts and": {"action": "split", "names": ["peanuts", "chopped fresh cilantro"]},
    "sour cream and": {"action": "split", "names": ["sour cream", "chopped parsley"]},
    "green onions and": {
        "action": "split",
        "names": ["sliced green onions", "diced avocado"],
        "preparation": "for serving",
    },
    "green onions and pico de gallo for serving": {
        "action": "split",
        "names": ["green onions", "pico de gallo"],
        "preparation": "for serving",
    },
    "sour cream and taco sauce for serving": {
        "action": "split",
        "names": ["sour cream", "taco sauce"],
        "preparation": "for serving",
    },
    "cocktail sauce and lemon wedges for serving": {
        "action": "split",
        "names": ["cocktail sauce", "lemon wedges"],
        "preparation": "for serving",
    },
    "nutmeg and whipped cream for serving": {
        "action": "split",
        "names": ["nutmeg", "whipped cream"],
        "preparation": "for serving",
    },
    "orange peel and cherries for serving": {
        "action": "split",
        "names": ["orange peel", "cherries"],
        "preparation": "for serving",
    },
    "lime slices and avocado slices for garnish": {
        "action": "split",
        "names": ["lime slices", "avocado slices"],
        "preparation": "for garnish",
    },
    "lime slices and cilantro sprigs for garnish": {
        "action": "split",
        "names": ["lime slices", "cilantro sprigs"],
        "preparation": "for garnish",
    },
    "chili crisp and sesame seeds for garnish": {
        "action": "split",
        "names": ["chili crisp", "sesame seeds"],
        "preparation": "for garnish",
    },
    "whole strawberries and blueberries for garnish": {
        "action": "split",
        "names": ["whole strawberries", "blueberries"],
        "preparation": "for garnish",
    },
    "sliced green onion and sesame seeds for garnish": {
        "action": "split",
        "names": ["sliced green onion", "sesame seeds"],
        "preparation": "for garnish",
    },
    "cocoa and powdered sugar for dusting top": {
        "action": "split",
        "names": ["cocoa", "powdered sugar"],
        "preparation": "for dusting",
    },
    "garnish with crushed red pepper flakes and slivered basil leaf": {
        "action": "split",
        "names": ["crushed red pepper flakes", "slivered basil leaves"],
        "preparation": "for garnish",
    },
    "garnish with fresh basil and crispy bacon": {
        "action": "split",
        "names": ["fresh basil", "crispy bacon"],
        "preparation": "for garnish",
    },
    "garnish with snipped chives and paprika": {
        "action": "split",
        "names": ["snipped chives", "paprika"],
        "preparation": "for garnish",
    },
    "garnish with fresh dill for flavor and presentation": {
        "action": "rename",
        "name": "fresh dill",
        "preparation": "for flavor and presentation",
    },
    "green onions and sesame seeds": {
        "action": "split",
        "names": ["green onion", "sesame seed"],
    },
    "green onions and toasted sesame seeds for garnish": {
        "action": "split",
        "names": ["sliced green onions", "toasted sesame seeds"],
        "preparation": "for garnish",
    },
    "toasted sesame seeds and": {"action": "split", "names": ["toasted sesame seeds", "sliced green onion"]},
    "maraschino cherries and": {
        "action": "split",
        "names": ["maraschino cherries", "chopped toasted pecans"],
        "preparation": "optional",
    },
    "milk chocolate chips or": {"action": "split", "names": ["milk chocolate chips", "chopped milk chocolate"]},
    "cooked bacon or": {
        "action": "split",
        "names": ["cooked bacon", "sliced smoked sausage"],
        "preparation": "for extra richness",
    },
    "drained capers or": {"action": "split", "names": ["drained capers", "chopped pickled jalapenos"]},
    "julienne carrot or": {"action": "split", "names": ["julienne carrot", "shredded carrot"]},
    "leeks or": {"action": "split", "names": ["sliced leeks", "chopped shallots"]},
    "parsley or": {"action": "split", "names": ["fresh parsley", "chopped scallions"]},
    "crumbled oaxaca or": {"action": "split", "names": ["crumbled oaxaca", "shredded monterrey jack cheese"]},
    "shredded fresh basil or": {
        "action": "split",
        "names": ["shredded fresh basil", "chopped fresh parsley"],
        "preparation": "optional",
    },
    "shredded lettuce and": {"action": "split", "names": ["shredded lettuce", "chopped tomato"]},
    "sriracha mayonnaise and": {"action": "split", "names": ["sriracha mayonnaise", "sliced scallions"]},
    "celery leaves and": {"action": "split", "names": ["celery leaves", "diced celery stalk"]},
    "coleslaw mix cabbage and": {"action": "split", "names": ["coleslaw mix cabbage", "shredded carrots"]},
    "thyme or": {"action": "split", "names": ["fresh thyme", "chopped chives"]},
    "whole or": {"action": "rename", "name": "walnut", "preparation": "whole or chopped"},
    "shredded turkey meat or use": {"action": "rename", "name": "shredded turkey", "preparation": "or use sliced deli turkey"},
    # broken rows with embedded junk -> rename to the food
    "and 2 tablespoon all-purpose flour": {"action": "rename", "name": "all-purpose flour"},
    "avocado or 2 small": {"action": "rename", "name": "avocado"},
    "handful or 2 spinach": {"action": "rename", "name": "spinach"},
    "or 2 fresno chili pepper": {"action": "rename", "name": "fresno chili pepper"},
    "or 2 small heads escarole": {"action": "rename", "name": "escarole"},
    "or 2 tablespoons hot water": {"action": "rename", "name": "hot water"},
    "or 3 green onion": {"action": "rename", "name": "green onion"},
    "or 30 ounce red enchilada sauce": {"action": "rename", "name": "red enchilada sauce"},
    "or 4 red pear": {"action": "rename", "name": "red pear"},
    "or medium egg": {"action": "rename", "name": "egg"},
    "liter vodka and bottle": {"action": "rename", "name": "vodka"},
    "ml gin and bottle": {"action": "rename", "name": "gin"},
    "quarts pineapple and orange juice blend": {"action": "rename", "name": "pineapple and orange juice blend"},
    "teaspoon or": {"action": "rename", "name": "crushed red pepper flakes", "preparation": "to taste"},
    "recipe sauerkraut filling or": {"action": "rename", "name": "sauerkraut filling"},
    "pitted black olives and/or": {"action": "rename", "name": "pitted black olives"},
    "black beans drain and reserve liquid": {
        "action": "rename",
        "name": "black bean",
        "preparation": "drain and reserve liquid",
    },
    "pineapple slices packed in juice drained and patted very very dry with paper towel": {
        "action": "rename",
        "name": "pineapple slices packed in juice",
        "preparation": "drained and patted dry with paper towels",
    },
    "coconut cream mix solid and liquid creams together before measuring": {
        "action": "rename",
        "name": "coconut cream",
        "preparation": "mix solid and liquid creams together before measuring",
    },
    "bell peppers any color stems and seeds removed": {
        "action": "rename",
        "name": "bell pepper",
        "preparation": "stems and seeds removed",
    },
    "poblano pepper - seeds and white ribs removed": {
        "action": "rename",
        "name": "poblano pepper",
        "preparation": "seeds and white ribs removed",
    },
    "green onions white and green part": {
        "action": "rename",
        "name": "green onion",
        "preparation": "white and green parts",
    },
    "stalks green onions white and light green parts only": {
        "action": "rename",
        "name": "green onion",
        "preparation": "white and light green parts only",
    },
    "strawberries hulled and": {"action": "rename", "name": "strawberry", "preparation": "hulled and chopped"},
    "whole and halved strawberry": {"action": "rename", "name": "strawberry", "preparation": "whole and halved"},
    "stemmed and thinly": {"action": "rename", "name": "kale", "preparation": "stemmed and thinly sliced"},
    "juice and zest from 1 lemon": {"action": "rename", "name": "lemon", "preparation": "juice and zest"},
    "zest and juice from one lemon": {"action": "rename", "name": "lemon", "preparation": "juice and zest"},
    "zest and juice of 1 lemon": {"action": "rename", "name": "lemon", "preparation": "juice and zest"},
    "zest and juice of 1/2 lemon": {"action": "rename", "name": "lemon", "preparation": "juice and zest"},
    "juice and zest of one lime": {"action": "rename", "name": "lime", "preparation": "juice and zest"},
    "zest and juice of 1/2 lime": {"action": "rename", "name": "lime", "preparation": "juice and zest"},
    "zest and juice of 1 orange": {"action": "rename", "name": "orange", "preparation": "juice and zest"},
    "lime juice and zest": {"action": "rename", "name": "lime", "preparation": "juice and zest"},
    "juice from boiled clams and mussel": {
        "action": "rename",
        "name": "clam juice",
        "preparation": "from boiled clams and mussels",
    },
    "meat and top shell of steamed blue crab": {"action": "rename", "name": "steamed blue crab"},
    "about 3 ounces frozen or thawed kataifi": {
        "action": "rename",
        "name": "kataifi",
        "preparation": "frozen or thawed",
    },
    "boursin garlic and herb gournay cheese fix branding": {
        "action": "rename",
        "name": "boursin garlic and herb gournay cheese",
    },
    "pkg chocolate instant pudding and pie filling such as jell-o2 1/2 cups heavy whipping cream": {
        "action": "rename",
        "name": "chocolate instant pudding and pie filling",
    },
    "fluid ounces sweet and sour mix": {"action": "rename", "name": "sweet and sour mix"},
    "fluid ounce 2 milk or almond milk": {"action": "rename", "name": "2% milk or almond milk"},
    "flour 00 and semolina blend is preferred": {
        "action": "rename",
        "name": "flour 00 and semolina blend",
        "preparation": "preferred",
    },
    # products and broken joke rows -> accepted as-is or as the food
    "kitty litter box and liner": {"action": "ignore"},
    "ox bat and ball": {"action": "ignore"},
    "aji nori furikake seasoned seaweed and sesame rice topping": {"action": "ignore"},
    "alouette garlic and herb spreadable cheese": {"action": "ignore"},
    "garlic and herb cheese spread": {"action": "ignore"},
    "garlic and herb cream cheese": {"action": "ignore"},
    "garlic and herb spreadable cheese": {"action": "ignore"},
    "herb and garlic cream cheese": {"action": "ignore"},
    "herb and garlic-flavored cream cheese": {"action": "ignore"},
    "chive and onion flavored cream cheese": {"action": "ignore"},
    "brown sugar and cinnamon spreadable cream cheese": {"action": "ignore"},
    "boursin shallot and chive spreadable cheese": {"action": "ignore"},
    "shallot and chive cheese spread": {"action": "ignore"},
    "pkg semi-soft cheese with garlic and fine herbs boursin": {"action": "ignore"},
    "hickory and brown sugar bbq sauce": {"action": "ignore"},
    "sweet and spicy barbecue sauce": {"action": "ignore"},
    "sweet and hot pepper": {"action": "ignore"},
    "sweet peppers and onion": {"action": "ignore"},
    "lemon and elderflower vodka": {"action": "ignore"},
    "basil and cracked black pepper chicken sausage": {"action": "ignore"},
    "smokey bacon and white cheddar sausage": {"action": "ignore"},
    "pkg garlic and herb chicken sausage": {"action": "ignore"},
    "evercrisp breader and batter boost": {"action": "ignore"},
    "teriyaki baste and glaze": {"action": "ignore"},
    "teriyaki marinade and sauce": {"action": "ignore"},
    "teriyaki marinade and sauce such as soy vey brand": {"action": "ignore"},
    "thick and chunky mild salsa": {"action": "ignore"},
    "wheat and barley nugget cereal such as grape-nut": {"action": "ignore"},
    "sweetened honey corn and oat cereal": {"action": "ignore"},
    "chocolate instant pudding and pie filling such as jello": {"action": "ignore"},
    "pkg vanilla instant pudding and pie mix such as jell-o": {"action": "ignore"},
    "pkg white cheddar macaroni and cheese": {"action": "ignore"},
    "prepared macaroni and cheese": {"action": "ignore"},
    "refrigerated macaroni and cheese": {"action": "ignore"},
    "macaroni and cheese": {"action": "ignore"},
    "uncooked long grain and wild rice mix": {"action": "ignore"},
    "packets instant maple and brown sugar oatmeal": {"action": "ignore"},
    "packet sazon goya with coriander and annatto": {"action": "ignore"},
    "sazon seasoning with coriander and achiote such as goya": {"action": "ignore"},
    "sazon with coriander and annatto": {"action": "ignore"},
    "sangrita mexican-style bloody mary mix with orange and lime": {"action": "ignore"},
    "shake and pour buttermilk pancake mix": {"action": "ignore"},
    "sugar-free cook and serve vanilla pudding mix": {"action": "ignore"},
    "vanilla yogurt such as dannon light and fit": {"action": "ignore"},
    "potatoes obrien with onions and pepper": {"action": "ignore"},
    "prepared sausage and oyster dressing": {"action": "ignore"},
    "ranch dressing and seasoning mix": {"action": "ignore"},
    "oil and vinegar dressing": {"action": "ignore"},
    "classic oil and vinegar salad dressing": {"action": "ignore"},
    "bottled oil and vinegar salad dressing such as newmans own": {"action": "ignore"},
    "red and green candy-coated chocolate pieces such as mms": {"action": "ignore"},
    "gluten-free baking flour such as premium gold flax and ancient grains all-purpose flour": {"action": "ignore"},
    "garlic and herb pasta sauce such as hunt": {"action": "ignore"},
    "garlic and herb seasoning blend such as mrs dash": {"action": "ignore"},
    "salt-free garlic and herb seasoning blend such as mrs dash": {"action": "ignore"},
    "roasted red pepper and garlic seasoning blend": {"action": "ignore"},
    "roasted red pepper and garlic tomato sauce": {"action": "ignore"},
    "classico tomato and basil pasta sauce": {"action": "ignore"},
    "tomatoes with basil and garlic": {"action": "ignore"},
    "tomatoes with basil garlic and olive oil": {"action": "ignore"},
    "tomatoes with garlic and oregano": {"action": "ignore"},
    "tomatoes with green pepper and onion": {"action": "ignore"},
    "tomatoes with green peppers and onion": {"action": "ignore"},
    "tomatoes and green chilies such as ro-tel": {"action": "ignore"},
    "original ro-tel tomatoes and green chili": {"action": "ignore"},
    "chilled tomato and clam juice cocktail such as clamato": {"action": "ignore"},
    "mushroom pieces and stem": {"action": "ignore"},
    "walnut halves and piece": {"action": "ignore"},
    "young yellow squash and zucchini": {"action": "ignore"},
    "assorted red and yellow sweet pepper": {"action": "ignore"},
    "mixed red and yellow bell pepper": {"action": "ignore"},
    "red and green chili pepper": {"action": "ignore"},
    "green and red sprinkle": {"action": "ignore"},
    "green and while candy sprinkle": {"action": "ignore"},
    "mini chocolate chips and sprinkle": {"action": "ignore"},
    "red and blue food coloring": {"action": "ignore"},
    "red and green food coloring": {"action": "ignore"},
    "red and/or green sprinkle": {"action": "ignore"},
    "mixed berries and fruit": {"action": "ignore"},
    "strawberries and cream liqueur such as bailey": {"action": "ignore"},
    "giblets and neck from turkey": {"action": "ignore"},
    "turkey neck and giblet": {"action": "ignore"},
    "chicken bone broth such as kettle and fire": {"action": "ignore"},
    "refrigerated crescent rolls such as pillsbury grands big and flaky": {"action": "ignore"},
    "bagoong isda with tomato and onion": {"action": "ignore"},
    "chopped peanut butter cups and coated peanut butter candies such as reeses pieces for topping": {"action": "ignore"},
    "sucralose and brown sugar blend such as splenda brown sugar blend": {"action": "ignore"},
    "sugar and sucralose blend for baking such as natur bakers blend": {"action": "ignore"},
    "sweet ginger and garlic seasoning": {"action": "ignore"},
    "hot cooked basmati rice and/or naan": {"action": "ignore"},
    "x18 inch piece of parchment paper or waxed paper": {"action": "ignore"},
    "cheese and garlic crouton": {"action": "ignore"},
    "grated parmesan and romano cheese blend": {"action": "ignore"},
    "grated parmesan cheese and romano cheese blend": {"action": "ignore"},
    "mixed mozzarella and sharp white cheddar cheese": {"action": "ignore"},
    "colby and sharp cheddar cheese": {"action": "ignore"},
    "shredded cheddar and monterey cheese blend": {"action": "ignore"},
    "shredded cheddar and monterey jack cheese": {"action": "ignore"},
    "shredded cheddar and swiss cheese mixture": {"action": "ignore"},
    "shredded colby and monterey jack cheese 4 oz": {"action": "ignore"},
    "shredded coleslaw mix with red and green cabbage and carrot": {"action": "ignore"},
    "colby and monterrey jack mixed cheese cube": {"action": "ignore"},
    "parmesan and romano cheese": {"action": "ignore"},
    "handful of mixed swiss and gruyre cheese": {"action": "ignore"},
    "cream of chicken and mushroom": {"action": "ignore"},
    "mixed berries and fruit": {"action": "ignore"},
    # "X or Y" choices with amounts on one side
    "1 teaspoon vanilla bean paste or extract": {"action": "ignore"},
    "50 less sodium beef broth or low-sodium chicken broth": {"action": "ignore"},
    "ab ghooreh or lemon juice plus 4 teaspoon zest": {"action": "ignore"},
    "apples or 1 large apple": {"action": "ignore"},
    "bag frozen artichokes or 2 cans drained": {"action": "ignore"},
    "bags green tea or 2 tablespoon loose green tea": {"action": "ignore"},
    "breadcrumbs or 3/4 cup panko breadcrumb": {"action": "ignore"},
    "campbells condensed cream of celery soup or campbells condensed 98 fat free cream of celery soup": {"action": "ignore"},
    "campbells condensed cream of mushroom soup or campbells condensed 98 fat free cream of mushroom soup": {"action": "ignore"},
    "campbells condensed cream of mushroom soup regular or 25 lower sodium": {"action": "ignore"},
    "campbells condensed cream of mushroom soup regular or 98 fat free": {"action": "ignore"},
    "cherry tomatoes or 1/2 cup tomatoes and 1/2 cup cucumber": {"action": "ignore"},
    "chicken bouillon cube or 2 teaspoons chicken bouillon paste": {"action": "ignore"},
    "chopped dill pickles or sweet pickle": {"action": "ignore"},
    "chopped fresh basil or 1 teaspoon dried basil": {"action": "ignore"},
    "chopped fresh cilantro or parsley": {"action": "ignore"},
    "chopped fresh oregano or basil": {"action": "ignore"},
    "chopped fresh parsley or chive": {"action": "ignore"},
    "chopped fresh parsley or cilantro": {"action": "ignore"},
    "chopped fresh parsley or fresh thyme": {"action": "ignore"},
    "chopped fresh thyme or 1/2 teaspoon dried thyme": {"action": "ignore"},
    "chopped green cabbage or kale": {"action": "ignore"},
    "chopped green onions or leek": {"action": "ignore"},
    "chopped kale or baby spinach": {"action": "ignore"},
    "chopped walnuts or hazelnut": {"action": "ignore"},
    "chopped walnuts or pecan": {"action": "ignore"},
    "crisp ladyfingers about 39 larger or 57 smaller ladyfinger": {"action": "ignore"},
    "crisp taco shells or 6 inch flour tortilla": {"action": "ignore"},
    "crushed tomatoes or 3 1/2 cups tomato sauce or puree": {"action": "ignore"},
    "dill or 1 teaspoon dried": {"action": "ignore"},
    "disks of homemade pie dough or 1 package pie dough": {"action": "ignore"},
    "dried campanelle or bowtie pasta 4 cup": {"action": "ignore"},
    "eggs or 2 cups egg white": {"action": "ignore"},
    "extra large flour tortilla 12 inches or larger": {"action": "ignore"},
    "finely crushed chocolate or regular graham crackers about 9 sheet": {"action": "ignore"},
    "firm fresh peaches or up to 6 if small": {"action": "ignore"},
    "fresh- squeezed lemon juice about 8 lemons or 1 12 ounce can lemonade concentrate": {"action": "ignore"},
    "garlic or 1 tsp garlic powder": {"action": "ignore"},
    "garlic powder or 1 fresh garlic clove": {"action": "ignore"},
    "garlic powder or 1 teaspoon garlic paste": {"action": "ignore"},
    "graham crackers about 13 crackers or 1 1/2 cups graham cracker crumb": {"action": "ignore"},
    "green cardamom pods or 1 teaspoon ground cardamom": {"action": "ignore"},
    "ground chuck or 80 lean ground beef": {"action": "ignore"},
    "ground lamb or 85 lean ground beef": {"action": "ignore"},
    "hibiscus tea bag or 3 tablespoons dried hibiscus flower": {"action": "ignore"},
    "in-thick bone-in lamb rib chops or 1 in-thick lamb loin chop": {"action": "ignore"},
    "japanese cucumber or 1/2 english cucumber": {"action": "ignore"},
    "kosher salt or 1 1/2 teaspoons fine salt": {"action": "ignore"},
    "kosher salt or 1 teaspoon per pound of meat": {"action": "ignore"},
    "kosher salt or 1/2 teaspoon iodized salt": {"action": "ignore"},
    "kosher salt or 1/2 teaspoon of fine table salt": {"action": "ignore"},
    "light beer or sparkling water 1 1/2 cup": {"action": "ignore"},
    "lime juice about 2 or 3 lime": {"action": "ignore"},
    "mascarpone or 4 ounces cream cheese": {"action": "ignore"},
    "milk or 1 high protein milk": {"action": "ignore"},
    "multicolored mini bell peppers or 1 large bell pepper": {"action": "ignore"},
    "pan drippings from roasted chicken or 1/4 cup butter": {"action": "ignore"},
    "percent or 99 percent lean ground beef": {"action": "ignore"},
    "plus 2 tablespoons self-rising flour or all-purpose flour plus 1 1/2 teaspoons baking powder": {"action": "ignore"},
    "plus 2/3 cup mixed berry jam or preserve": {"action": "ignore"},
    "pork loin or pork butt/shoulder": {"action": "ignore"},
    "prepared mashed sweet potatoes with cinnamon and brown sugar or about 2 cups of leftover mashed sweet potato casserole": {"action": "ignore"},
    "regular powdered fruit pectin or 6 tablespoons classic powdered fruit pectin such as ball": {"action": "ignore"},
    "sage or 1/2 teaspoon dried sage": {"action": "ignore"},
    "sea salt or kosher salt rounded 1 1/2 teaspoon": {"action": "ignore"},
    "sleeve of ritz crackers or 30 buttery rounds": {"action": "ignore"},
    "slices whole wheat bread or 8 bread roll": {"action": "ignore"},
    "slider buns or 6 hamburger bun": {"action": "ignore"},
    "stick or 112 g unsalted butter": {"action": "ignore"},
    "thyme or 1 tablespoon dried thyme": {"action": "ignore"},
    "thyme or 1 teaspoon dried thyme": {"action": "ignore"},
    "tri colored quinoa or rice about 1 1/2 cups cooked": {"action": "ignore"},
    "vanilla bean or 2 teaspoons vanilla extract": {"action": "ignore"},
    "vanilla bean paste or 1/2 tsp vanilla extract maple syrup": {"action": "ignore"},
    "vanilla extract or 1 teaspoon vanilla bean paste": {"action": "ignore"},
    "vanilla extract or 1/2 teaspoon vanilla bean paste": {"action": "ignore"},
    "yellow onion or 2 small yellow onion": {"action": "ignore"},
    "shredded mozzarella or mexican-blend cheese": {"action": "ignore"},
    "shredded potatoes or frozen hash brown": {"action": "ignore"},
    "shredded cabbage or coleslaw mix": {"action": "ignore"},
    "shredded lettuce or cabbage": {"action": "ignore"},
    "shredded wakame or hijiki seaweed": {"action": "ignore"},
    "shredded cooked chicken or turkey": {"action": "ignore"},
    "shredded raw spinach or whole baby spinach leaf": {"action": "ignore"},
    "skim or 1 milk": {"action": "ignore"},
    "sliced chives or green onion": {"action": "ignore"},
    "sliced strawberries or raspberry": {"action": "ignore"},
    "roughly-shredded lettuce such as romaine or iceberg": {"action": "ignore"},
    "shredded coconut sweetened or unsweetened": {"action": "ignore"},
    "basil and a drizzle of olive oil for garnish": {"action": "ignore"},
    "basil and garlic-flavored olive oil for brushing": {"action": "ignore"},
    "buns and desired toppings for serving": {"action": "ignore"},
    "shredded lettuce and any other desired topping": {"action": "ignore"},
    "shredded lettuce and crushed potato chips for serving": {"action": "ignore"},
    "soft unsalted butter for greasing the pan and parchment": {"action": "ignore"},
    "chicken wings flats and drummettes included": {"action": "ignore"},
    "poke leaves and tender stems peel stems if large": {"action": "ignore"},
    "turkey breast half with bones and skin": {"action": "ignore"},
    "roast-ready prime rib roast ribs cut off and tied to roast": {"action": "ignore"},
    "loosely packed fresh flat-leaf parsley leaves and tender stem": {"action": "ignore"},
    "packed fresh mint leaves and stem": {"action": "ignore"},
    "cilantro leaves and tender stem": {"action": "ignore"},
    "cilantro leaves and thinner stem": {"action": "ignore"},
    "flat-leaf parsley leaves and thinner stem": {"action": "ignore"},
    "bias-sliced sugar snap peas or snow pea": {"action": "ignore"},
    "bite-size pieces asparagus and/or frozen pea": {"action": "ignore"},
    "hot peppers and/or sweet pepper": {"action": "ignore"},
    "naan and/or microwavable rice": {"action": "ignore"},
    "vanilla ice cream and/or pineapple sherbert": {"action": "ignore"},
    "sunflower nuts and/or roasted pumpkin seeds": {"action": "ignore"},
    "sprinkles and/or more sanding sugar": {"action": "ignore"},
    "sesame seeds white and/or black": {"action": "ignore"},
    "shredded monterey jack and/or cheddar cheese": {"action": "ignore"},
    "shredded red and/or green cabbage": {"action": "ignore"},
    "shredded white and/or yellow sharp cheddar cheese": {"action": "ignore"},
    "milk chocolate and/or white chocolate chip": {"action": "ignore"},
    "slices fresh jalapeno and/or fresno chili ring": {"action": "ignore"},
    "hot and/or sweet pepper": {"action": "ignore"},
    "hearts of romaine and/or little gem lettuce": {"action": "ignore"},
    "watercress and/or arugula": {"action": "ignore"},
    "capers and/or cocktail onion": {"action": "ignore"},
    "capers and/or lemon wedge": {"action": "ignore"},
    "cauliflower and/or broccoli floret": {"action": "ignore"},
    "chili powder and/or cumin": {"action": "ignore"},
    "chopped celery and/or green olives and crumbled cotija cheese": {"action": "ignore"},
    "chopped fresh basil and/or oregano": {"action": "ignore"},
    "chopped fresh chives and/or grated parmesan cheese": {"action": "ignore"},
    "chopped fresh oregano and/or basil": {"action": "ignore"},
    "chopped fresh tarragon and/or parsley": {"action": "ignore"},
    "chopped green and/or red pepper": {"action": "ignore"},
    "chopped green onions and/or cilantro": {"action": "ignore"},
    "cilantro and/or toasted sesame seeds": {"action": "ignore"},
    "fuji and/or gala apple": {"action": "ignore"},
    "black and/or white sesame seeds": {"action": "ignore"},
    "cheddar and/or white cheddar cheese curds": {"action": "ignore"},
    "celery stick and/or other garnishe": {"action": "ignore"},
    "percent lean ground beef and/or ground pork": {"action": "ignore"},
    "szechuan peppercorns or other kick such as peppercorns and/or chile": {"action": "ignore"},
}

# Final round: leftovers after the long-tail pass (rename outputs that stay
# flagged, one-off artifacts, and temperature-junk water rows).
FINAL_ROUND_RULES: dict[str, dict] = {
    "all-purpose flour spooned and leveled": {"action": "rename", "name": "all-purpose flour", "preparation": "spooned and leveled"},
    "andouille sausage cut round or at an angle": {
        "action": "rename",
        "name": "andouille sausage",
        "preparation": "cut round or at an angle",
    },
    "assorted crackers and pretzel": {"action": "split", "names": ["assorted crackers", "pretzels"]},
    "bag trader joes peanut and crispy noodle salad kit": {"action": "ignore"},
    "boned and skinned chicken breast halve": {
        "action": "rename",
        "name": "chicken breast",
        "preparation": "boned and skinned",
    },
    "boneless skin-on turkey breast half about 2 and 1/2 lbs": {"action": "ignore"},
    "boursin garlic and herb gournay cheese": {"action": "ignore"},
    "broccoli florets and stalk": {"action": "rename", "name": "broccoli", "preparation": "florets and stalks"},
    "chocolate instant pudding and pie filling": {"action": "ignore"},
    "chopped chives or green onion": {"action": "ignore"},
    "chopped cilantro or other garnish": {"action": "ignore"},
    "chopped fresh basil leaves or parsley": {"action": "ignore"},
    "chopped fresh basil or flat-leaf parsley": {"action": "ignore"},
    "chopped fresh cilantro and avocado slice": {
        "action": "split",
        "names": ["chopped fresh cilantro", "avocado slices"],
    },
    "chopped fresh dill or other herb": {"action": "ignore"},
    "chopped fresh parsley and a sprinkle of red pepper flake": {
        "action": "split",
        "names": ["chopped fresh parsley", "red pepper flakes"],
        "preparation": "for garnish",
    },
    "chopped fresh thyme or": {"action": "rename", "name": "fresh thyme", "preparation": "chopped"},
    "chopped green onion and cilantro leaves for garnish": {
        "action": "split",
        "names": ["chopped green onion", "cilantro leaves"],
        "preparation": "for garnish",
    },
    "chopped green onion and parsley for garnish": {
        "action": "split",
        "names": ["chopped green onion", "parsley"],
        "preparation": "for garnish",
    },
    "chopped leeks white and pale green parts only": {
        "action": "rename",
        "name": "leek",
        "preparation": "chopped, white and pale green parts only",
    },
    "chopped scallions and sesame seeds for garnish": {
        "action": "split",
        "names": ["chopped scallions", "sesame seeds"],
        "preparation": "for garnish",
    },
    "cilantro sprigs and lime wedges for garnish": {
        "action": "split",
        "names": ["cilantro sprigs", "lime wedges"],
        "preparation": "for garnish",
    },
    "dill pickle chips or": {"action": "split", "names": ["dill pickle chips", "chopped pickles"], "preparation": "optional"},
    "first-cut brisket or flat-cut": {"action": "ignore"},
    "flat leaf parsley and lemon slice": {"action": "split", "names": ["flat-leaf parsley", "lemon slice"]},
    "flour 00 and semolina blend": {"action": "ignore"},
    "fluid ounces tomato and clam juice cocktail": {"action": "rename", "name": "tomato and clam juice cocktail"},
    "frozen mixed vegetables such as peas and carrot": {"action": "ignore"},
    "ground chicken white and dark meat": {
        "action": "rename",
        "name": "ground chicken",
        "preparation": "white and dark meat",
    },
    "hungarian sweet and spicy paprika": {"action": "ignore"},
    "kerrygold garlic and herb butter": {"action": "ignore"},
    "land o lakes butter with olive oil and sea salt": {"action": "ignore"},
    "licor 43 or kahla": {"action": "ignore"},
    "melted and cooled coconut oil/melted butter/melted ghee": {
        "action": "rename",
        "name": "coconut oil",
        "preparation": "melted and cooled; butter or ghee may be substituted",
    },
    "minced dill pickle or relish": {"action": "ignore"},
    "minced fresh basil or italian parsley": {"action": "ignore"},
    "minced fresh parsley or dill": {"action": "ignore"},
    "minced fresh thyme or rosemary": {"action": "ignore"},
    "oil and eggs called for one cake mix box": {
        "action": "rename",
        "name": "oil and eggs",
        "preparation": "as called for on the cake mix box",
    },
    "olive oil or use 1/4 cup lard for the traditional version": {"action": "ignore"},
    "paprika and fresh parsley if desired": {"action": "ignore"},
    "pasteurized liquid egg whites or 6 to 7 egg white": {"action": "ignore"},
    "peach slices and fresh blueberry": {
        "action": "split",
        "names": ["peach slices", "fresh blueberries"],
        "preparation": "optional",
    },
    "pkg refrigerated pizza dough or use a 7 oz purchased rectangle flatbread and skip ahead to step 2": {"action": "ignore"},
    "red berry sauce or seedless raspberry/strawberry jam": {"action": "ignore"},
    "slices bread and butter pickle": {"action": "rename", "name": "bread and butter pickle", "preparation": "sliced"},
    "strips cooked and crumbled bacon": {
        "action": "rename",
        "name": "bacon",
        "preparation": "strips, cooked and crumbled",
    },
    "tokwa and tuna": {"action": "split", "names": ["tokwa", "tuna"]},
    "virgin coconut oil melted and cooled": {
        "action": "rename",
        "name": "virgin coconut oil",
        "preparation": "melted and cooled",
    },
    "water 100 degrees f or 38 degrees c": {"action": "rename", "name": "water"},
    "water 105 to 115 degrees f or 41 to 46 degrees c": {"action": "rename", "name": "water"},
    "water 105-115 degrees f or 41 to 46 degrees c": {"action": "rename", "name": "water"},
    "whipping cream or 8 ounce frozen whipped dessert topping": {"action": "ignore"},
    "2 milk or almond milk": {"action": "ignore"},
    "oil and egg": {"action": "ignore"},
    # names regenerated after the embedded-measurement pass stripped the
    # amounts from previously-ignored forms
    "mixed berry jam or preserve": {"action": "ignore"},
    "crushed tomatoes or tomato sauce or puree": {"action": "ignore"},
    "stick or unsalted butter": {"action": "rename", "name": "unsalted butter"},
    "kosher salt or per pound of meat": {
        "action": "rename",
        "name": "kosher salt",
        "preparation": "1 teaspoon per pound of meat",
    },
    "olive oil or use lard for the traditional version": {"action": "ignore"},
    "fresh- squeezed lemon juice about 8 lemons or 1 can lemonade concentrate": {"action": "ignore"},
    "fresh- squeezed lemon juice about 8 lemons or lemonade concentrate": {"action": "ignore"},
    "graham crackers about 13 crackers or graham cracker crumb": {"action": "ignore"},
    "pkg refrigerated pizza dough or use a purchased rectangle flatbread and skip ahead to step 2": {"action": "ignore"},
    "vanilla bean paste or vanilla extract maple syrup": {"action": "ignore"},
    "prepared mashed sweet potatoes with cinnamon and brown sugar or leftover mashed sweet potato casserole": {"action": "ignore"},
    "pan drippings from roasted chicken or butter": {"action": "ignore"},
    "tri colored quinoa or rice cooked": {"action": "ignore"},
    "regular powdered fruit pectin or classic powdered fruit pectin such as ball": {"action": "ignore"},
    "hibiscus tea bag or dried hibiscus flower": {"action": "ignore"},
    "shredded colby and monterey jack cheese": {"action": "ignore"},
    "self-rising flour or all-purpose flour plus 1 baking powder": {"action": "ignore"},
    "chopped fresh basil or dried basil": {"action": "ignore"},
    "chopped fresh oregano or dried oregano": {"action": "ignore"},
    "chopped fresh thyme or dried thyme": {"action": "ignore"},
    "dill or dried": {"action": "ignore"},
    "thyme or dried thyme": {"action": "ignore"},
    "sage or dried sage": {"action": "ignore"},
    "garlic or garlic powder": {"action": "ignore"},
    "garlic powder or garlic paste": {"action": "ignore"},
    "mascarpone or cream cheese": {"action": "ignore"},
    "stick or unsalted butter": {"action": "rename", "name": "unsalted butter"},
    "bag frozen artichokes or drained": {"action": "rename", "name": "frozen artichokes", "preparation": "drained"},
}

# Ro*Tel-style diced tomatoes and green chiles: the canonicalizer folds the
# name to the singular ("tomatoes and green chile"), so both forms are
# pre-ignored to keep the product unflagged in future generations.
PRE_IGNORE = [
    "tomatoes and green chile",
    "tomatoes and green chiles",
    "tomato and clam juice cocktail",
    "flour 00 and semolina blend",
    "bread and butter pickle",
    "oil and eggs",
]

ROTEL_RENAMES = {
    "tomatoes and green chiles such as rotel": "tomatoes and green chiles",
    "tomatoes and green chile": "tomatoes and green chiles",
    "tomatoes and green chili": "tomatoes and green chiles",
}

# Canning-jar family: normalize to size + "canning jar".
JAR_SIZE_MAP = {
    "half-pint": "half-pint",
    "half pint": "half-pint",
    "pintning": "pint",
    "pint": "pint",
    "quart": "quart",
}


def jar_rename(cleaned_name: str) -> str | None:
    lowered = cleaned_name.lower()
    for brand in ("ball or kerr ", "ball ", "kerr "):
        if lowered.startswith(brand):
            lowered = lowered[len(brand):]
            break
    if lowered.startswith("mason jar"):
        return "mason jar"
    if lowered.startswith("canning jar"):
        return "canning jar"
    for size, canonical in JAR_SIZE_MAP.items():
        if lowered.startswith(size):
            return f"{canonical} canning jar"
    return None


OR_SUFFIX_RE = re.compile(r"^(?P<food>.+?)\s+or\s+(?P<prep>to taste|as needed|more(?:\s+to taste)?)$", re.IGNORECASE)
OIL_CHOICE_RE = re.compile(r"^(?P<a>[a-z0-9 ]+)\s+oil\s+or\s+(?P<b>[a-z0-9 ]+)\s+oil$", re.IGNORECASE)
# Choice names mentioning cheese on either side of an "or".
CHEESE_CHOICE_RE = re.compile(r"(?=.*\bcheese\b)(?=.*\bor\b)", re.IGNORECASE)
# 'all-purpose flour or bread flour' — letters, spaces, &, ', - only.
SIMPLE_CHOICE_RE = re.compile(r"^[a-z &'\-]+ or [a-z &'\-]+$", re.IGNORECASE)
PREP_JUNK_WORDS = {"cut", "chopped", "minced", "sliced", "diced", "crumbled", "shredded"}
# Leading descriptor runs that may precede a choice name.
CHOICE_LEAD_WORDS = PREP_JUNK_WORDS | {
    "finely", "thinly", "roughly", "coarsely", "freshly", "well", "toasted", "roasted",
    "canned", "frozen", "dried", "peeled", "seeded", "pitted", "cored", "drained", "rinsed",
    "cubed", "halved", "quartered", "trimmed", "cleaned", "large", "medium", "small",
}
CHOICE_TRAILING_NOTE_RE = re.compile(
    r"\s+(?:for (?:garnish|serving|topping|dipping|drizzling)|to serve|see tip)$", re.IGNORECASE
)


def choice_body(cleaned: str) -> str:
    """'finely chopped red or white onion' -> 'red or white onion'."""
    tokens = cleaned.split()
    while tokens and tokens[0].lower() in CHOICE_LEAD_WORDS:
        tokens.pop(0)
    return CHOICE_TRAILING_NOTE_RE.sub("", " ".join(tokens)).strip()
# "chili powder and/or cumin" — substitution pairs.
AND_OR_RE = re.compile(r"\band/or\b", re.IGNORECASE)


def suffix_choice_target(row_name: str) -> tuple[str, str] | None:
    """'sea salt or to taste' -> ('sea salt', 'to taste')."""
    match = OR_SUFFIX_RE.match(row_name)
    if not match:
        return None
    food = gc.clean_text(match.group("food"))
    if re.search(r"\b(?:or|and)\b", food, re.IGNORECASE):
        return None
    if not gc.normalize_name(food):
        return None
    return gc.singularize_phrase(food), gc.clean_text(match.group("prep"))


def choice_ignores() -> dict[str, dict]:
    """Generated ignore rules for choice names.

    * 'X oil or Y oil' (e.g. 'peanut oil or canola oil')
    * names containing 'cheese' plus a mid-name 'or'
    * simple two-item choices: 'all-purpose flour or bread flour'

    Names with preparation junk, embedded amounts, or 'to taste'/'as
    needed' suffixes are left for the interactive pass.
    """
    entries = load_json(gc.INGREDIENT_REVIEW_FILE)["entries"]
    rules: dict[str, dict] = {}
    for entry in entries:
        cleaned = entry.get("cleaned_name")
        if not isinstance(cleaned, str):
            continue
        normalized = gc.normalize_name(cleaned)
        if not normalized:
            continue
        if OIL_CHOICE_RE.match(cleaned) or OIL_CHOICE_RE.match(choice_body(cleaned)):
            rules.setdefault(normalized, {"action": "ignore"})
        elif CHEESE_CHOICE_RE.search(cleaned) and not re.search(r"\b(or|and)\s*$", cleaned):
            rules.setdefault(normalized, {"action": "ignore"})
        elif AND_OR_RE.search(cleaned):
            rules.setdefault(normalized, {"action": "ignore"})
        else:
            body = choice_body(cleaned)
            if SIMPLE_CHOICE_RE.match(body) and not re.search(r"\bor\s+(?:to taste|as needed|more)\b", body):
                rules.setdefault(normalized, {"action": "ignore"})
    return rules

# Preparation words that may lead a food name (aligned with the
# canonicalizer's PREPARATION_HINTS, plus state adjectives). Taste words
# (hot, sweet, sour) are deliberately absent: "hot and sour soup" and
# "hot and sweet pepper paste" are product names, not prep artifacts.
PREP_RUN_WORDS = gc.PREPARATION_HINTS | {
    "cooked", "fresh", "frozen", "hulled", "toasted", "roasted",
    "salted", "unsalted", "raw", "finely", "coarsely", "thickly",
    "thinly", "rinsed", "cored",
}

# Words a food name cannot begin with after the prep-run is stripped
# (embedded amounts / unit-ish leftovers).
BAD_REMAINDER_STARTS = {
    "about", "rounded", "heaping", "generous", "roughly",
}
# Count nouns that precede the food after a prep-run ("strips cooked and
# crumbled bacon" -> "bacon").
LEADING_COUNT_NOUNS = {"strips", "strip", "slices", "slice", "pieces", "piece"}


def prep_artifact_rename(row_name: str) -> str | None:
    """Food name = row name minus a preparation run that contains a
    connector, e.g. 'peeled and deveined shrimp' -> 'shrimp',
    'medium peeled and deveined shrimp' -> 'medium shrimp'.

    The run starts at the first preparation word; leading descriptor words
    (sizes etc.) stay in the remainder.
    """
    tokens = row_name.split()
    if not tokens:
        return None
    # Brand notes and semicolon junk are not reliably restorable.
    if "such as" in row_name.lower() or ";" in row_name:
        return None
    start = None
    for index, token in enumerate(tokens):
        if token.lower() in PREP_RUN_WORDS:
            start = index
            break
    if start is None:
        return None
    run: list[str] = []
    saw_connector = False
    for token in tokens[start:]:
        lowered = token.lower()
        if lowered in PREP_RUN_WORDS or lowered in {"and", "or"}:
            run.append(token)
            if lowered in {"and", "or"}:
                saw_connector = True
            continue
        break
    if not saw_connector or not run:
        return None
    after = tokens[start + len(run):]
    remainder = " ".join(tokens[:start] + after)
    # A leftover connector means the structure was "prep A and prep B" with
    # a food in between ("chopped fresh cilantro and sliced green onions") —
    # that is a real compound, not a prep artifact.
    if re.search(r"\b(?:and|or)\b", remainder):
        return None
    # Skip when the food still leads with an embedded amount or unit-ish
    # leftover ("2 fresno chili peppers", "about 3 ounces kataifi",
    # "rounded tablespoon ...") — the quantity belongs in the row.
    first_word = remainder.split()[0].lower() if remainder.split() else ""
    if (
        not first_word
        or first_word in BAD_REMAINDER_STARTS
        or first_word in LEADING_COUNT_NOUNS
        or re.match(r"^\d+(?:\.\d+)?(?:\s+\d+/\d+)?$", first_word)
    ):
        return None
    # Drop size notes like "21 to 30 per pound" from shrimp rows.
    remainder = re.sub(r"\s+\d+(?:\s+to\s+\d+)?\s*per\s+(?:pound|lb)s?\b.*$", "", remainder)
    if not gc.normalize_name(remainder):
        return None
    return gc.singularize_phrase(gc.clean_text(remainder))


def build_by_entry() -> list[dict]:
    entries = load_json(gc.INGREDIENT_REVIEW_FILE)["entries"]
    decisions: list[dict] = []
    counts: Counter[str] = Counter()
    for entry in order_review_entries(entries):
        if "ambiguous_connector" not in entry.get("issue_types", []):
            continue
        recipe_id = entry.get("recipe_id")
        position = entry.get("position")
        recipe_path = gc.RECIPES_DIR / f"{recipe_id}.json"
        if not recipe_path.exists() or not isinstance(position, int):
            continue
        recipe = gc.load_json(recipe_path)
        rows = recipe.get("ingredients", [])
        row = next((r for r in rows if r.get("position") == position), None)
        if row is None or not isinstance(row.get("name"), str):
            continue
        target = prep_artifact_rename(row["name"])
        if target is not None and target != gc.normalize_name(row["name"]):
            counts[row["name"]] += 1
            decisions.append(
                {"recipe_id": recipe_id, "position": position, "action": "rename", "name": target}
            )
            continue
        suffix = suffix_choice_target(row["name"])
        if suffix is not None:
            target, preparation = suffix
            counts[row["name"]] += 1
            decisions.append(
                {
                    "recipe_id": recipe_id,
                    "position": position,
                    "action": "rename",
                    "name": target,
                    "preparation": preparation,
                }
            )
    print("prep-artifact renames by source row name:")
    for name, count in counts.most_common(20):
        print(f"  {count:>4}  {name!r}")
    return decisions


# Manual per-row overrides for rows the heuristics cannot restore.
BY_ENTRY_OVERRIDES: dict[tuple[str, int], dict] = {
    # comma-split put the food in preparation
    ("b60a9f0d-1090-5d54-b0d1-8491f690f1e7", 1): {
        "action": "rename",
        "name": "cooked chicken meat",
        "preparation": "diced and chilled",
    },
    # dash artifact in the row name
    ("fb1d3973-601a-5d65-8b79-d578bcbd8e1a", 5): {
        "action": "rename",
        "name": "bacon",
        "preparation": "cooked and crumbled",
    },
}


def build_jar_renames() -> list[dict]:
    """By-name renames for the canning-jar family ("pintning jars with lids
    and ring" -> "pint canning jar")."""
    entries = load_json(gc.INGREDIENT_REVIEW_FILE)["entries"]
    seen: dict[str, str] = {}
    for entry in entries:
        cleaned = entry.get("cleaned_name")
        if not isinstance(cleaned, str) or "jar" not in cleaned.lower():
            continue
        target = jar_rename(cleaned)
        if target is not None:
            seen.setdefault(cleaned, target)
    return seen


def build_alias_renames() -> list[dict]:
    """Rows whose only issue is an alias parenthetical ("chopped chicken
    (from 1 rotisserie chicken)") are renamed to the canonical cleaned name
    so the parenthetical stops regenerating the entry."""
    entries = load_json(gc.INGREDIENT_REVIEW_FILE)["entries"]
    decisions: list[dict] = []
    for entry in entries:
        if "alias_parenthetical" not in entry.get("issue_types", []):
            continue
        cleaned = entry.get("cleaned_name")
        if isinstance(cleaned, str) and cleaned and gc.normalize_name(cleaned):
            decisions.append(
                {
                    "recipe_id": entry.get("recipe_id"),
                    "position": entry.get("position"),
                    "action": "rename",
                    "name": cleaned,
                }
            )
    return decisions


# Third round: leftovers after the class-rule pass (products with "and",
# real pairs to split, and prep notes jammed after the food).
ROUND_3_RULES: dict[str, dict] = {
    "semi-soft cheese with garlic and fine herbs boursin": {"action": "ignore"},
    "mixed swiss and gruyre cheese": {"action": "ignore"},
    "vanilla instant pudding and pie mix such as jell-o": {"action": "ignore"},
    "finely shredded cheddar and monterey jack cheese blend": {"action": "ignore"},
    "white cheddar macaroni and cheese": {"action": "ignore"},
    "garlic and herb chicken sausage": {"action": "ignore"},
    "canned diced tomatoes and green chile": {"action": "ignore"},
    "refrigerated pizza dough or use a purchased rectangle flatbread and skip ahead to step 2": {
        "action": "ignore",
    },
    "dried minced onion such as mccormick coarse grind blend white and green onion": {"action": "ignore"},
    "thinly sliced onion and tomatoes for serving": {
        "action": "split",
        "names": ["thinly sliced onion", "tomatoes"],
        "preparation": "for serving",
    },
    "thinly sliced red onion and sliced banana peppers for topping": {
        "action": "split",
        "names": ["thinly sliced red onion", "sliced banana peppers"],
        "preparation": "for topping",
    },
    "finely chopped red onion and fresh cilantro": {
        "action": "split",
        "names": ["finely chopped red onion", "fresh cilantro"],
    },
    "thinly sliced scallions and toasted sesame seeds": {
        "action": "split",
        "names": ["sliced scallions", "toasted sesame seeds"],
    },
    "carrot cut in half and sliced thin": {
        "action": "rename",
        "name": "carrot",
        "preparation": "cut in half and sliced thin",
    },
    "carrots cut in half and into thin stick": {
        "action": "rename",
        "name": "carrot",
        "preparation": "cut in half and into thin sticks",
    },
    "canned sliced pineapple rings drain and reserve juice": {
        "action": "rename",
        "name": "canned sliced pineapple rings",
        "preparation": "drain and reserve juice",
    },
    "finely chopped green onion white and light green part": {
        "action": "rename",
        "name": "green onion",
        "preparation": "finely chopped, white and light green parts",
    },
    "finely chopped green onion white and light green parts only": {
        "action": "rename",
        "name": "green onion",
        "preparation": "finely chopped, white and light green parts only",
    },
    "finely chopped green onions white and light green parts only": {
        "action": "rename",
        "name": "green onion",
        "preparation": "finely chopped, white and light green parts only",
    },
    "finely sliced green onions white and light green parts only": {
        "action": "rename",
        "name": "green onion",
        "preparation": "finely sliced, white and light green parts only",
    },
    "thinly sliced green onions white and light green parts only": {
        "action": "rename",
        "name": "green onion",
        "preparation": "thinly sliced, white and light green parts only",
    },
    "pitted and sliced peaches": {"action": "rename", "name": "peach", "preparation": "pitted and sliced"},
    "cored and chopped red tomatoes": {"action": "rename", "name": "tomato", "preparation": "cored and chopped"},
    "large frozen peeled and deveined shrimp": {
        "action": "rename",
        "name": "shrimp",
        "preparation": "large, frozen, peeled and deveined",
    },
    "shredded lettuce and crushed potato chip": {
        "action": "split",
        "names": ["shredded lettuce", "crushed potato chips"],
    },
    "lime wedges and parsley or cilantro sprig": {
        "action": "split",
        "names": ["lime wedges", "parsley"],
        "preparation": "or cilantro sprigs, for garnish",
    },
    "buns and desired topping": {"action": "ignore"},
    "chopped peanut butter cups and coated peanut butter candies such as reeses piece": {"action": "ignore"},
    "basil and a drizzle of olive oil": {"action": "ignore"},
}


def main() -> None:
    by_name = dict(sorted(BY_NAME.items()))
    for source, target in ROTEL_RENAMES.items():
        by_name[source] = {"action": "rename", "name": target}
    for source, target in build_jar_renames().items():
        by_name.setdefault(source, {"action": "rename", "name": target})
    for normalized, rule in choice_ignores().items():
        by_name.setdefault(normalized, rule)
    for source, rule in LONG_TAIL_RULES.items():
        by_name.setdefault(gc.normalize_name(source), rule)
    for source, rule in FINAL_ROUND_RULES.items():
        by_name.setdefault(gc.normalize_name(source), rule)
    for source, rule in ROUND_3_RULES.items():
        by_name.setdefault(gc.normalize_name(source), rule)
    payload = {
        "schema_version": 1,
        "by_name": by_name,
        "by_entry": build_by_entry()
        + build_alias_renames()
        + [
            {"recipe_id": recipe_id, "position": position, **decision}
            for (recipe_id, position), decision in sorted(BY_ENTRY_OVERRIDES.items())
        ],
        "pre_ignore": sorted(PRE_IGNORE),
    }
    DECISIONS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {DECISIONS_PATH} "
        f"({len(payload['by_name'])} by_name rules, {len(payload['by_entry'])} by_entry renames, "
        f"{len(payload['pre_ignore'])} pre-ignored names)"
    )


if __name__ == "__main__":
    sys.exit(main())