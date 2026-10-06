import time
from typing import TYPE_CHECKING

from nomad.config import config
from nomad.datamodel.data import ArchiveSection, Schema, UseCaseElnCategory
from nomad.datamodel.metainfo.annotations import ELNAnnotation, ELNComponentEnum
from nomad.datamodel.metainfo.basesections import (
    BaseSection,
    Entity,
    EntityReference,
)
from nomad.datamodel.results import ELN, Results
from nomad.metainfo import MEnum, Quantity, SchemaPackage
from nomad.metainfo.metainfo import Section, SubSection
from nomad.units import ureg

from nomad_tajine_plugin.schema_packages.foodon_lookup.foodon_lookup import (
    get_diet_type_from_superclasses,
    get_foodon_match,
    get_foodon_substitutes,
)
from nomad_tajine_plugin.schema_packages.usda_lookup.usda_lookup import get_usda_data
from nomad_tajine_plugin.utils import create_archive

if TYPE_CHECKING:
    from nomad.datamodel.datamodel import (
        EntryArchive,
    )
    from structlog.stdlib import (
        BoundLogger,
    )


configuration = config.get_plugin_entry_point(
    'nomad_tajine_plugin.schema_packages:schema_tajine_entry_point'
)

m_package = SchemaPackage()


def format_lab_id(lab_id: str):
    return lab_id.lower().replace(' ', '_').replace(',', '')


def add_eln_tags(archive: 'EntryArchive', tags: list[str]):
    """Adds tags to results.eln.tags so they show up in NOMAD's search."""
    # results can still be None here on some reprocessing paths.
    if not tags:
        return
    if archive.results is None:
        archive.results = Results()
    if archive.results.eln is None:
        archive.results.eln = ELN()
    if archive.results.eln.tags is None:
        archive.results.eln.tags = []
    for tag in tags:
        if tag not in archive.results.eln.tags:
            archive.results.eln.tags.append(tag)


class Ingredient(Entity, Schema):
    """
    An ingredient used in cooking recipes.
    """

    m_def = Section(
        label='Ingredient Type',
        categories=[UseCaseElnCategory],
    )
    density = Quantity(
        type=float,
        a_eln=ELNAnnotation(component=ELNComponentEnum.NumberEditQuantity),
        unit='g/L',
        default=1000.0,  # default to water density for now
    )
    weight_per_piece = Quantity(
        type=float,
        a_eln=ELNAnnotation(component=ELNComponentEnum.NumberEditQuantity),
        unit='g',
        default=50.0,  # default to 50 g per piece for now
    )
    diet_type = Quantity(
        type=MEnum(
            'omnivorous',
            'vegetarian',
            'vegan',
            'ambiguous',
        ),
        a_eln=ELNAnnotation(component=ELNComponentEnum.EnumEditQuantity),
    )
    calories_per_100_g = Quantity(
        type=float,
        unit='kcal',
        description='Nutrients per 100 g for this ingredient type imported from USDA.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='kcal'
        ),
    )
    fat_per_100_g = Quantity(
        type=float,
        unit='g',
        description='Nutrients per 100 g for this ingredient type imported from USDA.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='g'
        ),
    )
    protein_per_100_g = Quantity(
        type=float,
        unit='g',
        description='Nutrients per 100 g for this ingredient type imported from USDA.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='g'
        ),
    )
    carbohydrates_per_100_g = Quantity(
        type=float,
        unit='g',
        description='Nutrients per 100 g for this ingredient type imported from USDA.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='g'
        ),
    )
    fdc_id = Quantity(
        type=int,
        a_eln=ELNAnnotation(component=ELNComponentEnum.NumberEditQuantity),
    )
    ndb_id = Quantity(
        type=int,
        a_eln=ELNAnnotation(component=ELNComponentEnum.NumberEditQuantity),
    )
    foodon_iri = Quantity(
        type=str,
        description="""Matched FoodOn (https://foodon.org) class IRI for this
            ingredient, resolved via nomad-ontology-service.""",
        a_eln=ELNAnnotation(component=ELNComponentEnum.StringEditQuantity),
    )
    foodon_label = Quantity(
        type=str,
        description='Label of the matched FoodOn class.',
        a_eln=ELNAnnotation(component=ELNComponentEnum.StringEditQuantity),
    )
    foodon_substitutes = Quantity(
        type=str,
        shape=['*'],
        description="""Suggested substitutes: other FoodOn classes sharing the
            matched class's immediate parent, e.g. other spices for a spice.""",
    )

    def normalize(self, archive: 'EntryArchive', logger: 'BoundLogger'):
        if not self.lab_id:
            if self.name:
                self.lab_id = format_lab_id(self.name)
        else:
            self.lab_id = format_lab_id(self.lab_id)

        usda_query_result = get_usda_data(self.name, configuration.usda_api_key)
        if usda_query_result:
            self.protein_per_100_g = usda_query_result.get('protein')
            self.fat_per_100_g = usda_query_result.get('fat')
            self.carbohydrates_per_100_g = usda_query_result.get('carbohydrates')
            self.calories_per_100_g = usda_query_result.get('calories_kcal')
            self.diet_type = usda_query_result.get('diet_type')
            self.fdc_id = usda_query_result.get('fdc_id')
            self.ndb_id = usda_query_result.get('ndb_id')

        # Clear old values so a reprocess without a match doesn't keep them.
        self.foodon_iri = None
        self.foodon_label = None
        self.foodon_substitutes = []

        foodon_match = get_foodon_match(self.name)
        if foodon_match:
            self.foodon_iri = foodon_match['iri']
            self.foodon_label = foodon_match['label']

            foodon_diet_type = get_diet_type_from_superclasses(foodon_match['iri'])
            if foodon_diet_type:
                self.diet_type = foodon_diet_type

            self.foodon_substitutes = get_foodon_substitutes(
                foodon_match['iri'], foodon_match['label']
            )

        tags = []
        if self.diet_type:
            tags.append(f'diet:{self.diet_type}')
        if self.foodon_label:
            tags.append(f'foodon:{self.foodon_label}')
        add_eln_tags(archive, tags)

        super().normalize(archive, logger)


class IngredientAmount(EntityReference):
    """
    Represents the amount of an ingredient in a recipe.
    """

    name = Quantity(
        type=str, a_eln=ELNAnnotation(component=ELNComponentEnum.StringEditQuantity)
    )
    mass = Quantity(
        type=float,
        unit='gram',
        description='The mass of the ingredient',
        a_eln=ELNAnnotation(component=ELNComponentEnum.NumberEditQuantity),
    )
    lab_id = Quantity(
        type=str,
        description="""An ID string that is unique at least for the lab that produced
            this data.""",
        a_eln=dict(component='StringEditQuantity', label='ingredient ID'),
    )
    reference = Quantity(
        type=Ingredient,
        description='A reference to a ingredient type entry.',
        a_eln=ELNAnnotation(
            component='ReferenceEditQuantity',
            label='ingredient type reference',
        ),
    )
    diet_type = Quantity(
        type=MEnum(
            'omnivorous',
            'vegetarian',
            'vegan',
            'ambiguous',
        ),
        a_eln=ELNAnnotation(component=ELNComponentEnum.EnumEditQuantity),
    )
    calories = Quantity(
        type=float,
        unit='kcal',
        description='Total calories of this ingredient.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity,
            defaultDisplayUnit='kcal',
        ),
    )
    fat = Quantity(
        type=float,
        unit='g',
        description='Total fat of this ingredient.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='g'
        ),
    )
    protein = Quantity(
        type=float,
        unit='g',
        description='Total proteins of this ingredient.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='g'
        ),
    )
    carbohydrates = Quantity(
        type=float,
        unit='g',
        description='Total carbohydrates of this ingredient.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='g'
        ),
    )

    def calculate_nutrients(self, logger):
        for nutrient in ('calories', 'fat', 'protein', 'carbohydrates'):
            try:
                per_100_g_attr = f'{nutrient}_per_100_g'
                value_per_100_g = getattr(self.reference, per_100_g_attr)
                value = (self.mass * value_per_100_g / ureg.Quantity(100, 'gram')).to(
                    value_per_100_g.units
                )
                setattr(self, nutrient, value)
            except TypeError:
                logger.warn(
                    f'Failed to calculate {nutrient} for ingredient {self.name}',
                    exc_info=True,
                )

    def normalize(self, archive: 'EntryArchive', logger: 'BoundLogger'):
        """
        For the given ingredient name or ID, fetches the corresponding Ingredient entry.
        If not found, creates a new Ingredient entry. Converts the quantity to SI units
        based on the unit and ingredient properties like density or weight per piece.
        """
        if not self.lab_id:
            if self.name:
                self.lab_id = format_lab_id(self.name)
        else:
            self.lab_id = format_lab_id(self.lab_id)

        super().normalize(archive, logger)
        if (
            not self.reference
            and self.lab_id
            and hasattr(archive.data, '_normalization_delay')
        ):  # Wait and search again for ingredient if _normalization_delay is set
            time.sleep(archive.data._normalization_delay)
            super().normalize(archive, logger)

        if not self.reference and self.lab_id:
            logger.debug('Ingredient entry not found. Creating a new one.')
            try:
                ingredient = Ingredient(
                    name=self.name,
                    lab_id=self.lab_id,
                )
                self.reference = create_archive(
                    ingredient,
                    archive,
                    f'{self.lab_id}.archive.json',
                    overwrite=False,
                )
            except Exception as e:
                logger.error(
                    'Failed to create Ingredient entry.', exc_info=True, error=e
                )

        if self.reference:
            self.diet_type = self.reference.diet_type
            if self.mass:
                self.calculate_nutrients(logger)


class IngredientVolume(IngredientAmount):
    volume = Quantity(
        type=float,
        unit='milliliter',
        description='The volume of the ingredient that should be used.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity,
            defaultDisplayUnit='milliliter',
        ),
    )
    mass = Quantity(  # Overload to remove ELN annotation
        type=float,
        unit='gram',
        description='The mass of the ingredient that should be used.',
    )

    def normalize(self, archive: 'EntryArchive', logger: 'BoundLogger'):
        super().normalize(archive, logger)
        if self.reference and self.reference.density:
            self.mass = self.volume * self.reference.density
            self.calculate_nutrients(logger)


class IngredientPiece(IngredientAmount):
    pieces = Quantity(
        type=float,
        description='The number of pieces of the ingredient that should be used.',
        a_eln=ELNAnnotation(component=ELNComponentEnum.NumberEditQuantity),
    )
    mass = Quantity(  # Overload to remove ELN annotation
        type=float,
        unit='gram',
        description='The mass of the ingredient that should be used.',
    )

    def normalize(self, archive: 'EntryArchive', logger: 'BoundLogger'):
        super().normalize(archive, logger)
        if self.reference and self.reference.weight_per_piece:
            self.mass = self.pieces * self.reference.weight_per_piece
            self.calculate_nutrients(logger)


class Tool(ArchiveSection):
    """
    A kitchen tool or utensil used in cooking.
    """

    name = Quantity(
        type=str, a_eln=ELNAnnotation(component=ELNComponentEnum.StringEditQuantity)
    )
    type = Quantity(
        type=str, a_eln=ELNAnnotation(component=ELNComponentEnum.StringEditQuantity)
    )
    description = Quantity(
        type=str, a_eln=ELNAnnotation(component=ELNComponentEnum.StringEditQuantity)
    )


class RecipeStep(ArchiveSection):
    """
    A single step in a cooking recipe.
    """

    duration = Quantity(
        type=float,
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='minute'
        ),
        unit='minute',
    )
    tools = SubSection(
        section_def=Tool,
        description='',
        repeats=True,
    )
    ingredients = SubSection(
        section_def=IngredientAmount,
        description='',
        repeats=True,
    )
    instruction = Quantity(
        type=str, a_eln=ELNAnnotation(component=ELNComponentEnum.RichTextEditQuantity)
    )


class HeatingCoolingStep(RecipeStep):
    """
    A recipe step that involves heating or cooling to a specific temperature.
    """

    temperature = Quantity(
        type=float,
        default=20.0,
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='celsius'
        ),
        unit='celsius',
    )


class Recipe(BaseSection, Schema):
    """
    A schema representing a cooking recipe with ingredients, tools, and steps.
    """

    m_def = Section(
        label='Cooking Recipe',
        categories=[UseCaseElnCategory],
        a_eln=ELNAnnotation(hide=['_normalization_delay']),
    )
    name = Quantity(
        type=str, a_eln=ELNAnnotation(component=ELNComponentEnum.StringEditQuantity)
    )
    duration = Quantity(
        type=float,
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='minute'
        ),
        unit='minute',
    )
    authors = Quantity(
        type=str, a_eln=ELNAnnotation(component=ELNComponentEnum.StringEditQuantity)
    )
    difficulty = Quantity(
        type=MEnum(
            'easy',
            'medium',
            'hard',
        ),
        a_eln=ELNAnnotation(component=ELNComponentEnum.EnumEditQuantity),
    )
    number_of_servings = Quantity(
        type=int, a_eln=ELNAnnotation(component=ELNComponentEnum.NumberEditQuantity)
    )
    summary = Quantity(
        type=str, a_eln=ELNAnnotation(component=ELNComponentEnum.RichTextEditQuantity)
    )
    cuisine = Quantity(
        type=str, a_eln=ELNAnnotation(component=ELNComponentEnum.StringEditQuantity)
    )
    diet_type = Quantity(
        type=MEnum(
            'omnivorous',
            'vegetarian',
            'vegan',
            'ambiguous',
        ),
        a_eln=ELNAnnotation(component=ELNComponentEnum.EnumEditQuantity),
    )
    calories = Quantity(
        type=float,
        unit='kcal',
        description='Total calories of this ingredient.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity,
            defaultDisplayUnit='kcal',
        ),
    )
    fat = Quantity(
        type=float,
        unit='g',
        description='Total fat of this recipe.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='g'
        ),
    )
    protein = Quantity(
        type=float,
        unit='g',
        description='Total proteins of this recipe.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='g'
        ),
    )
    carbohydrates = Quantity(
        type=float,
        unit='g',
        description='Total carbohydrates of this recipe.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity, defaultDisplayUnit='g'
        ),
    )
    calories_per_serving = Quantity(
        type=float,
        unit='kcal',
        description='Calories per serving.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity,
            defaultDisplayUnit='kcal',
        ),
    )
    fat_per_serving = Quantity(
        type=float,
        unit='g',
        description='Fats per serving.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity,
            defaultDisplayUnit='g',
        ),
    )
    protein_per_serving = Quantity(
        type=float,
        unit='g',
        description='Proteins per serving.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity,
            defaultDisplayUnit='g',
        ),
    )
    carbohydrates_per_serving = Quantity(
        type=float,
        unit='g',
        description='Carbohydrates per serving.',
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity,
            defaultDisplayUnit='g',
        ),
    )
    duration = Quantity(
        type=float,
        a_eln=ELNAnnotation(
            component=ELNComponentEnum.NumberEditQuantity,
            defaultDisplayUnit='minute',
        ),
        unit='minute',
    )
    tools = SubSection(
        section_def=Tool,
        description='',
        repeats=True,
    )
    steps = SubSection(
        section_def=RecipeStep,
        description='',
        repeats=True,
    )
    ingredients = SubSection(
        section_def=IngredientAmount,
        description='',
        repeats=True,
    )
    _normalization_delay = Quantity(
        type=float,
        default=0.0,
    )

    def generate_description(self) -> None:
        """
        Generates an HTML formatted step-by-step instructions based on the recipe steps.
        """
        self.description = '<ol>'
        for step in self.steps:
            self.description += f'<li>{step.instruction}</li>'
        self.description += '</ol>'

    def normalize(self, archive: 'EntryArchive', logger: 'BoundLogger') -> None:  # noqa: PLR0912
        """
        Collects all ingredients and tools from steps and adds them to the recipe's
        ingredients and tools lists.
        """
        super().normalize(archive, logger)

        all_ingredients = []
        all_tools = []

        for step in self.steps:
            for ingredient in step.ingredients:
                # Check if ingredient with the same name exists
                existing = next(
                    (ing for ing in all_ingredients if ing.name == ingredient.name),
                    None,
                )

                if existing is None:
                    all_ingredients.append(ingredient)
                else:
                    # Sum quantities
                    new_quantity = (existing.quantity or 0) + (ingredient.quantity or 0)

                    # Sum nutrient values safely
                    nutrients = {}
                    for nutrient in ('calories', 'fat', 'protein', 'carbohydrates'):
                        nutrients[nutrient] = (getattr(existing, nutrient, 0) or 0) + (
                            getattr(ingredient, nutrient, 0) or 0
                        )

                    # Create a new ingredient with summed values
                    ingredient_summed = IngredientAmount(
                        name=existing.name,
                        quantity=new_quantity,
                        unit=existing.unit,
                        mass=None,  # optionally recalc
                        lab_id=existing.lab_id,
                        reference=existing.reference,
                        **nutrients,
                    )

                    # Replace old ingredient with new summed one
                    all_ingredients = [
                        ing if ing.name != ingredient.name else ingredient_summed
                        for ing in all_ingredients
                    ]

            for tool in step.tools:
                existing = next((tl for tl in all_tools if tl.name == tool.name), None)
                if existing is None:
                    all_tools.append(tool)

        self.ingredients.extend(
            IngredientAmount.m_from_dict(ingredient.m_to_dict())
            for ingredient in all_ingredients
        )
        self.tools.extend(Tool.m_from_dict(tool.m_to_dict()) for tool in all_tools)

        # --- Compute total nutrients ---
        for nutrient in ('calories', 'fat', 'protein', 'carbohydrates'):
            setattr(
                self,
                nutrient,
                sum(
                    (getattr(ingredient, nutrient, 0.0) or 0.0)
                    for ingredient in (self.ingredients or [])
                ),
            )

        # --- Compute nutrients per serving ---
        if self.number_of_servings:
            for nutrient in ('calories', 'fat', 'protein', 'carbohydrates'):
                per_serving_attr = f'{nutrient}_per_serving'
                total_value = getattr(self, nutrient, 0.0)
                setattr(self, per_serving_attr, total_value / self.number_of_servings)

        # --- Compute total duration ---
        try:
            self.duration = sum((step.duration or 0.0) for step in (self.steps or []))
        except Exception as e:
            logger.warning('recipe_duration_sum_failed', error=str(e))

        ingredient_diets = [
            (ingredient.diet_type or 'ambiguous')
            for ingredient in (self.ingredients or [])
        ]

        # --- Find the diet type ---
        if not ingredient_diets:
            self.diet_type = 'ambiguous'
        elif 'omnivorous' in ingredient_diets:
            self.diet_type = 'omnivorous'
        elif all(d == 'vegan' for d in ingredient_diets):
            self.diet_type = 'vegan'
        elif 'vegetarian' in ingredient_diets:
            self.diet_type = 'vegetarian'
        else:
            self.diet_type = 'ambiguous'

        tags = [f'diet:{self.diet_type}']
        if self.cuisine:
            tags.append(f'cuisine:{self.cuisine}')
        add_eln_tags(archive, tags)

        self.generate_description()


class RecipeScaler(BaseSection, Schema):
    """
    A schema that references an existing recipe and creates a scaled version
    based on desired number of servings.
    """

    m_def = Section(
        label='Recipe Scaler',
        description='Scale a recipe for different serving sizes',
    )
    original_recipe = Quantity(
        type=Recipe,
        description='Reference to the original recipe to be scaled',
        a_eln=ELNAnnotation(component=ELNComponentEnum.ReferenceEditQuantity),
    )
    desired_servings = Quantity(
        type=int,
        description='Number of servings desired for the scaled recipe',
        a_eln=ELNAnnotation(component=ELNComponentEnum.NumberEditQuantity),
    )
    scaled_recipe = Quantity(
        type=Recipe,
        description='The resulting scaled recipe',
    )

    def scale_recipe(
        self,
        recipe: Recipe,
        scaling_factor: float,
        archive: 'EntryArchive',
        logger: 'BoundLogger',
    ) -> None:
        """
        Scales the given recipe by the specified scaling factor and creates
        a new archived entry for the scaled recipe.
        """
        if scaling_factor == 1.0:
            logger.warning('Scaling factor is 1.0, no scaling applied.')
            return
        scaled_recipe = Recipe().m_from_dict(recipe.m_to_dict(with_root_def=True))
        scaled_recipe.name += f' (scaled x{scaling_factor:.2f})'
        scaled_recipe.number_of_servings *= scaling_factor

        # reset ingredients and tools, that will be populated from steps
        scaled_recipe.tools = []
        scaled_recipe.ingredients = []

        # Scale ingredients in steps
        for step in scaled_recipe.steps:
            for ingredient in step.ingredients:
                ingredient.quantity *= scaling_factor
                if ingredient.quantity_si:
                    ingredient.quantity_si *= scaling_factor

        file_name = (
            (f'{recipe.name} scaled x{scaling_factor:.2f}.archive.json')
            .replace(' ', '_')
            .lower()
        )
        self.scaled_recipe = create_archive(
            scaled_recipe, archive=archive, file_name=file_name, overwrite=True
        )

    def normalize(self, archive: 'EntryArchive', logger: 'BoundLogger') -> None:
        """
        Uses the referenced original recipe entry and specified desired servings to
        create a scaled recipe entry.
        """
        super().normalize(archive, logger)

        self.scaled_recipe = None
        if self.original_recipe and self.desired_servings:
            try:
                scaling_factor = (
                    self.desired_servings / self.original_recipe.number_of_servings
                )
                self.scale_recipe(self.original_recipe, scaling_factor, archive, logger)
            except Exception as e:
                logger.error('Error while scaling recipe.', exc_info=True, error=e)


m_package.__init_metainfo__()
