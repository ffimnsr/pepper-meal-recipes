import sys
from pathlib import Path
from unittest import TestCase


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "py-scripts"))

from ingredient_repair import fix_cleanup_row, is_cleanup_row  # noqa: E402


def row(name, quantity=None, unit=None, preparation=None):
    return {"name": name, "quantity": quantity, "unit": unit, "preparation": preparation}


class CleanupDetectionTests(TestCase):
    def test_detects_cleanup_families(self) -> None:
        names = [
            "fluid ounces tequila",
            "quarts water",
            "quarts cold water",
            "large cloves garlic",
            "fresh cloves garlic",
            "water 110 degrees f",
            "water as needed",
            "water at room temperature",
            "scoop vanilla protein powder",
            "tablepoons teriyaki sauce",
            "teasoons ketchup",
            "tablespoonketchup",
            "all-purposeflour",
            "about 4 red onion slivers",
            "optional 1 cinnamon stick",
            "10” flour tortillas",
            "pack of 10 in flour tortillas",
            "!/2 lime",
            "2% low-fat milk",
            "93-lean ground turkey",
            "small 3- lobster tails",
            "7.. bag crunchy corn snacks such as bugles",
            "pepperoni slices such as hormel from 1 pkg.",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(is_cleanup_row(row(name)))

    def test_ignores_legit_size_and_product_names(self) -> None:
        names = [
            "large eggs",
            "medium onion",
            "pound cake",
            "scoop-shaped tortilla chips such as tostitos scoop",
            "pinto bean",
            "ground beef",
            "ground cinnamon",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertFalse(is_cleanup_row(row(name)))

    def test_percent_names_are_detected_but_kept(self) -> None:
        # Percent products are deliberately kept; the fixer must be a no-op.
        names = [
            "2% milk",
            "85% lean ground beef",
            "100% pure pumpkin",
            'italian tipo "00" flour',
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(is_cleanup_row(row(name)))
                self.assertIsNone(fix_cleanup_row(row(name, quantity="1", unit="cup")))

    def test_detects_blocked_preparation_leads(self) -> None:
        names = [
            "fresh minced parsley",
            "large chopped onion",
            "bunch chopped parsley",
            "dash crushed red pepper",
            "raw cleaned whole pumpkin seeds",
            ". thinly sliced pepperoni",
            "thinly shaved parmesan cheese",
        ]
        for name in names:
            with self.subTest(name=name):
                self.assertTrue(is_cleanup_row(row(name)))
        self.assertTrue(is_cleanup_row(row("cored", preparation="peeled, and diced apples")))
        self.assertTrue(is_cleanup_row(row("large peeled", preparation="deveined raw shrimp")))

    def test_digit_milk_names_keep_the_percent_convention(self) -> None:
        # "1 milk"/"2 milk" are the catalog's digit forms of 1%/2% milk;
        # quantity and unit are already structured, so the digit must not
        # move into the quantity.
        for name, quantity, unit in [("1 milk", "1", "cup"), ("2 milk", "1", "gallon")]:
            with self.subTest(name=name):
                self.assertFalse(is_cleanup_row(row(name)))
                self.assertIsNone(fix_cleanup_row(row(name, quantity=quantity, unit=unit)))


class CleanupFixTests(TestCase):
    def assert_fixed(self, name, expected: dict, **kwargs) -> None:
        fixed = fix_cleanup_row(row(name, **kwargs))
        self.assertIsNotNone(fixed, f"expected fix for {name!r}")
        for key, value in expected.items():
            self.assertEqual(fixed[key], value, f"{name!r} -> {key}")

    def test_unit_word_lead_moves_unit(self) -> None:
        self.assert_fixed(
            "fluid ounces tequila",
            {"name": "tequila", "unit": "ounce", "quantity": "6"},
            quantity="6",
            unit="pieces",
        )
        self.assert_fixed("quarts water", {"name": "water", "unit": "quart"}, quantity="3", unit="pieces")
        self.assert_fixed(
            "pint cherry tomatoes",
            {"name": "cherry tomatoes", "unit": "pint", "preparation": "halved"},
            quantity="1",
            unit="piece",
            preparation="halved",
        )
        self.assert_fixed(
            "quart vegetable oil for deep-frying",
            {"name": "vegetable oil", "unit": "quart", "preparation": "for deep-frying"},
            quantity="1",
            unit="piece",
        )

    def test_unit_word_lead_keeps_existing_real_unit(self) -> None:
        self.assertIsNone(fix_cleanup_row(row("quarts water", quantity="3", unit="quart")))

    def test_temp_state_strips_to_preparation(self) -> None:
        self.assert_fixed(
            "quarts cold water",
            {"name": "water", "unit": "quart", "preparation": "cold"},
            quantity="3",
            unit="pieces",
        )
        self.assert_fixed(
            "gallon cold water",
            {"name": "water", "unit": "gallon", "preparation": "cold"},
            quantity="2",
            unit="pieces",
        )

    def test_water_notes(self) -> None:
        self.assert_fixed(
            "water 110 degrees f",
            {"name": "water", "preparation": "warm", "unit": "cup"},
            quantity="0.25",
            unit="cup",
        )
        self.assert_fixed("water as needed", {"name": "water", "preparation": "as needed"})
        self.assert_fixed(
            "water at room temperature",
            {"name": "water", "preparation": "room temperature"},
            quantity="0.5",
            unit="cup",
        )

    def test_clove_garlic_family(self) -> None:
        self.assert_fixed(
            "large cloves garlic",
            {"name": "garlic", "unit": "clove", "preparation": "minced"},
            quantity="2",
            unit="pieces",
            preparation="minced",
        )
        self.assert_fixed(
            "fresh cloves garlic",
            {"name": "garlic", "unit": "clove", "preparation": "minced"},
            quantity="3",
            unit="pieces",
            preparation="minced",
        )
        self.assert_fixed(
            "cloves garlic clove",
            {"name": "garlic", "unit": "clove"},
            quantity="6",
            unit="pieces",
        )

    def test_scoop_and_typo_leads(self) -> None:
        self.assert_fixed(
            "scoop vanilla protein powder",
            {"name": "vanilla protein powder"},
            quantity="1",
            unit="piece",
        )
        self.assert_fixed(
            "tablepoons teriyaki sauce",
            {"name": "teriyaki sauce", "unit": "tablespoon"},
            quantity="3",
            unit="pieces",
        )
        self.assert_fixed(
            "table spoon shoaxing cooking wine",
            {"name": "shaoxing cooking wine", "unit": "tablespoon"},
            quantity="3",
        )
        self.assert_fixed(
            "teasoons ketchup",
            {"name": "ketchup", "unit": "tablespoon", "preparation": "divided"},
            quantity="1",
            unit="tablespoon",
            preparation="divided",
        )
        self.assert_fixed(
            "teaspooon finely minced garlic",
            {"name": "garlic", "unit": "teaspoon", "preparation": "finely minced"},
            quantity="1",
            unit="piece",
        )

    def test_glued_unit_and_flour(self) -> None:
        self.assert_fixed(
            "tablespoonketchup",
            {"name": "ketchup", "unit": "tablespoon"},
            quantity="1",
            unit="piece",
        )
        self.assert_fixed(
            "tablespoonrice vinegar",
            {"name": "rice vinegar", "unit": "tablespoon"},
            quantity="1",
            unit="piece",
        )
        self.assert_fixed(
            "all-purposeflour",
            {"name": "all-purpose flour", "quantity": "2 cups plus 1 tablespoon"},
            quantity="2 cups plus 1 tablespoon",
        )

    def test_prefix_junk(self) -> None:
        self.assert_fixed(
            "optional 1 cinnamon stick",
            {"name": "cinnamon stick"},
            unit="pieces",
        )
        self.assert_fixed(
            "10” flour tortillas",
            {"name": "flour tortillas", "quantity": "6"},
            quantity="6",
            unit="pieces",
        )
        self.assert_fixed(
            "pack of 10 in flour tortillas",
            {"name": "flour tortillas", "quantity": "1"},
            quantity="1",
            unit="piece",
        )
        self.assert_fixed(
            "pack 12 count king’s hawaiian sweet rolls",
            {"name": "king’s hawaiian sweet rolls", "quantity": "1"},
            quantity="1",
            unit="piece",
        )
        self.assert_fixed(
            "small 3- lobster tails",
            {"name": "lobster tails", "quantity": "1"},
            quantity="1",
            unit="piece",
        )
        self.assert_fixed(
            "7.. bag crunchy corn snacks such as bugles",
            {"name": "crunchy corn snacks such as bugles", "unit": "bag", "quantity": "1"},
            quantity="1",
            unit="piece",
        )

    def test_bang_fraction_and_amount(self) -> None:
        self.assert_fixed("!/2 lime", {"name": "lime", "quantity": "1/2"})

    def test_about_lead(self) -> None:
        self.assert_fixed(
            "about 4 red onion slivers",
            {"name": "red onion", "preparation": "or to taste, slivered"},
            preparation="or to taste",
        )
        self.assert_fixed(
            "about 30 pepperoni slices such as hormel from 1 6 oz pkg",
            {"name": "pepperoni slices such as hormel"},
        )

    def test_packaging_note_off_name(self) -> None:
        self.assert_fixed(
            "pepperoni slices such as hormel from 1 pkg.",
            {"name": "pepperoni slices such as hormel", "quantity": "3", "unit": "oz"},
            quantity="3",
            unit="oz",
        )
        self.assert_fixed(
            "refrigerated shredded hash browns from 1 pkg. [1] [2] [3]",
            {"name": "refrigerated shredded hash browns"},
            quantity="5",
            unit="cups",
        )

    def test_percent_and_chocolate_renames(self) -> None:
        self.assert_fixed("1% fat milk", {"name": "1% milk", "quantity": "1", "unit": "cup"}, quantity="1", unit="cup")
        self.assert_fixed("2% plain greek yogurt", {"name": "plain greek yogurt"}, quantity="1/2", unit="cup")
        self.assert_fixed(
            "85% organic",
            {"name": "85% lean ground beef", "preparation": "grass-fed organic"},
            quantity="3",
            unit="pounds",
            preparation="grass-fed organic",
        )
        self.assert_fixed(
            "85% lean ground beef chuc",
            {"name": "85% lean ground beef"},
            quantity="1",
            unit="pound",
        )
        self.assert_fixed("70% dark chocolate", {"name": "dark chocolate"}, quantity="2", unit="ounces")
        self.assert_fixed(
            "56% cacao semisweet chocolate bar",
            {"name": "semisweet chocolate bar"},
            quantity="7",
            unit="oz",
        )
        self.assert_fixed(
            "2% evaporated milk",
            {"name": "evaporated milk", "quantity": "24", "unit": "ounce"},
            quantity="12",
            unit="ounce",
        )
        self.assert_fixed(
            "93-lean ground turkey",
            {"name": "93 lean ground turkey"},
            quantity="20",
            unit="ounce",
        )

    def test_packaging_and_of_leads(self) -> None:
        self.assert_fixed(
            "pkg. chopped romaine hearts",
            {"name": "romaine hearts", "quantity": "9 to 10", "unit": "ounce", "preparation": "chopped"},
            quantity="9 to 10",
            unit="ounce",
        )
        self.assert_fixed(
            "pk frozen puff pastry",
            {"name": "frozen puff pastry", "unit": "ounce"},
            quantity="17.3",
            unit="ounce",
        )
        self.assert_fixed(
            "pk pkg kings hawaiian roll",
            {"name": "kings hawaiian roll", "unit": "package"},
            quantity="2 12",
            unit="package",
        )
        self.assert_fixed(
            "small bunch of cilantro",
            {"name": "cilantro", "unit": "bunch"},
            quantity="1",
            unit="piece",
        )
        self.assert_fixed(
            "dash of garlic powder",
            {"name": "garlic powder"},
        )

    def test_as_needed_and_optional_notes(self) -> None:
        self.assert_fixed("oil as needed", {"name": "oil", "preparation": "as needed"})
        self.assert_fixed(
            "oil as needed to seal edges",
            {"name": "oil", "preparation": "as needed to seal edges"},
        )
        self.assert_fixed(
            "cornstarch as needed for dusting optional",
            {"name": "cornstarch", "preparation": "optional, as needed for dusting"},
        )
        self.assert_fixed("oil for fryin", {"name": "oil for frying"})

    def test_descriptor_swap_and_peeling(self) -> None:
        self.assert_fixed(
            "skinless",
            {"name": "boneless chicken breasts", "preparation": "skinless, cut into 1-inch cubes"},
            quantity="1",
            unit="pound",
            preparation="boneless, boneless chicken breasts, cut into 1-inch cubes",
        )
        self.assert_fixed(
            "cooked",
            {"name": "chicken meat", "preparation": "cooked, cubed"},
            quantity="1.5",
            unit="pounds",
            preparation="cubed chicken meat",
        )
        self.assert_fixed(
            "coarsely chopped",
            {"name": "kalamata olives", "preparation": "coarsely chopped, pitted"},
            quantity="0.75",
            unit="cup",
            preparation="pitted kalamata olives",
        )
        self.assert_fixed(
            "frozen",
            {"name": "ready-to-cook chicken wings", "preparation": "frozen"},
            quantity="1",
            unit="pound",
            preparation="ready-to-cook chicken wings",
        )

    def test_glued_ounce_and_comma_plus(self) -> None:
        self.assert_fixed(
            "ouncecan tomato paste",
            {"name": "tomato paste", "unit": "ounce"},
            quantity="6",
            unit="pieces",
        )
        self.assert_fixed(
            "ounceed white cake mix",
            {"name": "white cake mix", "unit": "ounce"},
            quantity="15.25",
            unit="pieces",
        )
        self.assert_fixed(
            "ouncesmilk chocolate",
            {"name": "milk chocolate", "unit": "ounce"},
            quantity="6",
            unit="pieces",
        )
        fixed = fix_cleanup_row(
            row("cup, plus 1 tablespoon canola oil, divided", quantity="1/4", unit="cup", preparation="plus 1 tablespoon canola oil, divided")
        )
        self.assertIsNotNone(fixed)
        self.assertEqual(fixed["quantity"], "1/4 cup plus 1 tablespoon")
        self.assertEqual(fixed["unit"], None)
        self.assertEqual(fixed["name"], "canola oil")
        self.assertEqual(fixed["preparation"], "divided")
        fixed = fix_cleanup_row(
            row("cup, plus 3 tablespoon ketchup,", quantity="1/4", unit="cup", preparation="plus 3 tablespoon ketchup, divided")
        )
        self.assertIsNotNone(fixed)
        self.assertEqual(fixed["quantity"], "1/4 cup plus 3 tablespoons")
        self.assertEqual(fixed["name"], "ketchup")

    def test_percent_and_spice_variants(self) -> None:
        self.assert_fixed(
            "72% dark chocolate chips such as ghirardelli",
            {"name": "dark chocolate chips such as ghirardelli"},
            quantity="6",
            unit="ounces",
        )
        self.assert_fixed(
            "93%-lean ground beef",
            {"name": "93 lean ground beef"},
            quantity="1",
            unit="pound",
        )
        self.assert_fixed(
            "cloves",
            {"name": "ground cloves", "unit": "teaspoon"},
            quantity="1/8",
            unit="teaspoon",
        )

    def test_leading_preparation_moves_to_preparation_field(self) -> None:
        self.assert_fixed(
            "finely chopped onion",
            {"name": "onion", "preparation": "finely chopped"},
            quantity="1",
            unit="cup",
        )
        self.assert_fixed(
            "minced garlic",
            {"name": "garlic", "preparation": "minced"},
            quantity="2",
            unit="cloves",
        )
        self.assert_fixed(
            "freshly ground black pepper",
            {"name": "black pepper", "preparation": "freshly ground"},
        )
        self.assert_fixed(
            "quartered strawberries",
            {"name": "strawberries", "preparation": "quartered"},
            quantity="1",
            unit="cup",
        )
        self.assert_fixed("mincedginger", {"name": "ginger", "preparation": "minced"})
        self.assert_fixed(
            "shredded mozzarellacheese",
            {"name": "mozzarella cheese", "preparation": "shredded"},
        )
        self.assertIsNone(
            fix_cleanup_row(row("ground beef", quantity="1", unit="pound")),
            "bare 'ground' is a product form and must not move",
        )

    def test_blocked_preparation_leads_move_verb(self) -> None:
        self.assert_fixed(
            "fresh minced parsley",
            {"name": "fresh parsley", "preparation": "minced"},
            quantity="2",
            unit="tablespoons",
        )
        self.assert_fixed(
            "large chopped onion",
            {"name": "large onion", "preparation": "chopped"},
            quantity="1",
            unit="piece",
        )
        self.assert_fixed(
            "bunch minced green onions",
            {"name": "green onions", "unit": "bunch", "preparation": "minced"},
            quantity="1",
            unit="piece",
        )
        self.assert_fixed(
            "dash crushed red pepper",
            {"name": "red pepper", "unit": "dash", "preparation": "crushed"},
            unit="pieces",
        )
        self.assert_fixed(
            "raw cleaned whole pumpkin seeds",
            {"name": "raw whole pumpkin seeds", "preparation": "cleaned"},
            quantity="1",
            unit="cup",
        )
        self.assert_fixed(
            ". thinly sliced pepperoni",
            {"name": "pepperoni", "preparation": "thinly sliced"},
            quantity="0.75",
            unit="cup",
        )
        self.assert_fixed(
            "thinly shaved parmesan cheese",
            {"name": "parmesan cheese", "preparation": "thinly shaved"},
        )
        self.assert_fixed(
            "thinly bias-sliced green onions",
            {"name": "green onions", "preparation": "thinly bias-sliced"},
            quantity="3",
            unit="tablespoons",
        )
        self.assert_fixed(
            "finely pre-shredded italian cheese blend",
            {"name": "italian cheese blend", "preparation": "finely pre-shredded"},
            quantity="1",
            unit="cup",
        )
        self.assert_fixed(
            "2% reduced-fat milk",
            {"name": "reduced-fat milk"},
            quantity="0.75",
            unit="cup",
        )

    def test_lone_preparation_name_swaps_with_food(self) -> None:
        self.assert_fixed(
            "cored",
            {"name": "apples", "preparation": "cored, peeled, diced"},
            quantity="2.5",
            unit="cups",
            preparation="peeled, and diced apples",
        )
        self.assert_fixed(
            "large peeled",
            {"name": "raw shrimp", "preparation": "peeled, deveined"},
            quantity="1",
            unit="pound",
            preparation="deveined raw shrimp",
        )

    def test_numeral_and_size_leads(self) -> None:
        self.assert_fixed(
            "i can chunk chicken",
            {"name": "chunk chicken", "quantity": "1", "unit": "can"},
            unit="pieces",
        )
        self.assert_fixed(
            "i cup water",
            {"name": "water", "quantity": "1", "unit": "cup"},
            unit="pieces",
        )
        self.assert_fixed(
            "i sheet frozen puff pastry",
            {"name": "frozen puff pastry", "quantity": "1", "unit": "sheet"},
            unit="pieces",
        )
        self.assert_fixed(
            "3/4 inch pieces peeled butternut squash from 1 small squash",
            {"name": "butternut squash", "quantity": "3", "unit": "cups", "preparation": "cubed, from 1 small squash, peeled"},
            quantity="3",
            unit="cups",
            preparation="cubed",
        )
        self.assert_fixed(
            "shallot from 1 large shallot",
            {"name": "shallot", "preparation": "from 1 large shallot"},
            quantity="0.5",
            unit="cup",
        )

    def test_idempotent(self) -> None:
        cases = [
            row("fluid ounces tequila", quantity="6", unit="pieces"),
            row("quarts cold water", quantity="3", unit="pieces"),
            row("large cloves garlic", quantity="2", unit="pieces"),
            row("water 110 degrees f", quantity="0.25", unit="cup"),
            row("2% low-fat milk", quantity="0.25", unit="cup"),
            row("scoop vanilla protein powder", quantity="1", unit="piece"),
            row("7.. bag crunchy corn snacks such as bugles", quantity="1", unit="piece"),
            row("!/2 lime"),
        ]
        for original in cases:
            with self.subTest(name=original["name"]):
                fixed = fix_cleanup_row(original)
                self.assertIsNotNone(fixed)
                self.assertIsNone(
                    fix_cleanup_row(fixed),
                    f"second pass should be a no-op for {original['name']!r}",
                )

    def test_blocked_lead_fixes_are_idempotent(self) -> None:
        cases = [
            row("fresh minced parsley", quantity="2", unit="tablespoons"),
            row("large chopped onion", quantity="1", unit="piece"),
            row("bunch minced green onions", quantity="1", unit="piece"),
            row("dash crushed red pepper", quantity="1", unit="piece"),
            row("raw cleaned whole pumpkin seeds", quantity="1", unit="cup"),
            row(". thinly sliced pepperoni", quantity="0.75", unit="cup"),
            row("2% reduced-fat milk", quantity="0.75", unit="cup"),
            row("cored", quantity="2.5", unit="cups", preparation="peeled, and diced apples"),
            row("large peeled", quantity="1", unit="pound", preparation="deveined raw shrimp"),
        ]
        for original in cases:
            with self.subTest(name=original["name"]):
                fixed = fix_cleanup_row(original)
                self.assertIsNotNone(fixed)
                self.assertIsNone(
                    fix_cleanup_row(fixed),
                    f"second pass should be a no-op for {original['name']!r}",
                )

    def test_no_rule_returns_none(self) -> None:
        self.assertIsNone(fix_cleanup_row(row("large eggs", quantity="4", unit="pieces")))
        self.assertIsNone(fix_cleanup_row(row("pinto bean", quantity="15", unit="ounce")))
        self.assertIsNone(fix_cleanup_row(row("100% pure pumpkin", quantity="1", unit="can")))