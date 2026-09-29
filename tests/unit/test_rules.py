"""Rule tests grounded in real titles from data/samples/gold_labels_pilot.xlsx.
See docs/classification_guidelines.md for why each rule exists."""

from dmie.classification.rules import apply_rules


def test_base_plate_wax_is_relevant():
    title = "General USE 20PCS 270g Medium Soft Base Plate Wax Molding Casting Wax Sheet Modeling Filling NO Denture"
    assert apply_rules(title) == ("RELEVANT", "EXACT_MATCH")


def test_denture_base_resin_is_relevant():
    assert apply_rules("Antinsky Denture Base Resin") == ("RELEVANT", "EXACT_MATCH")


def test_denture_base_repair_is_relevant():
    title = "Hard Denture Reline Kit – Long-Lasting Denture Base Repair & Fit Adjustment, Acrylic-Based, Self-Curing, Translucent Pink"
    assert apply_rules(title) == ("RELEVANT", "EXACT_MATCH")


def test_denture_base_renewal_is_relevant():
    title = "Self-Curing Hard Denture Reline Kit for Home Use, Complete Denture Base Renewal and Fit Adjustment Set"
    assert apply_rules(title) == ("RELEVANT", "EXACT_MATCH")


def test_adhesive_is_irrelevant():
    title = "Denture Adhesive Material, Gum Base for Dental Appliances and False Teeth"
    assert apply_rules(title) == ("IRRELEVANT", "WRONG_CATEGORY")


def test_bonding_glue_is_irrelevant():
    title = "Dental Lab Instant Bonding Glue for Dentures & Lab Tools – Fast-Setting"
    assert apply_rules(title) == ("IRRELEVANT", "WRONG_CATEGORY")


def test_ultrasonic_cleaner_is_irrelevant_despite_containing_base():
    title = "Ultrasonic Retainer Cleaner Machine with 4 Modes – 45kHz 180ML Ultrasonic Cleaner for Retainer,Denture,Mouth Guard,Ring,Jewelry,Leak-Proof Detachable Tank & Base, Easy-to-Clean"
    assert apply_rules(title) == ("IRRELEVANT", "WRONG_CATEGORY")


def test_screwdriver_bit_holder_is_unrelated():
    title = "Denture Drill Bit Holder with Base,Dentist Gift,Mouth Bit Holder,28PCS 1/4” Hex Bit, 2-in-1 Screwdriver"
    assert apply_rules(title) == ("IRRELEVANT", "UNRELATED")


def test_tooth_repair_beads_is_irrelevant():
    title = "Tooth Repair Kit, Moldable False Teeth Beads for Teeth Repair, Suitable for Missing, Cracked DIY Denture Kit"
    assert apply_rules(title) == ("IRRELEVANT", "WRONG_CATEGORY")


def test_general_denture_repair_kit_is_irrelevant():
    title = "Dentemp Repair Kit - Repair-It Advanced Formula Denture Repair Kit - Repairs Broken Dentures, Mends Cracks and Replace Loose Teeth"
    assert apply_rules(title) == ("IRRELEVANT", "WRONG_CATEGORY")


def test_polishing_burs_is_accessory_only():
    title = "12pcs Silicone Composite Polishing Heads 3/32\" Shaft Resin Base Acrylic Denture Polishing Burs Finishing Kits"
    assert apply_rules(title) == ("IRRELEVANT", "ACCESSORY_ONLY")


# --- escape hatch: reline/refit/full-denture/mold signals defer to AI, never forced NO ---

def test_reline_mention_is_not_forced_irrelevant_even_with_repair_kit_phrase():
    title = "Silicone Reline Denture Set, Silicone Denture Set, Silicone Reline Kit for Dentures, Denture Repair Kit, Soft Silicone Denture Reline Kit (1 PCS)"
    assert apply_rules(title) is None


def test_reline_it_bundle_is_not_forced_irrelevant():
    title = "Dentemp Repair-it Denture Repair Kit & Reline-it Denture Reliner - Denture Kit (Multi-Pack)"
    assert apply_rules(title) is None


def test_refit_liners_mention_is_not_forced_irrelevant_despite_bead_language():
    title = "Sukh 10.9oz Thermoplastic Denture Beads - Moldable False Teeth Beads | Tooth Repair Kit, Thermoplastic Beads for Tooth Filling, Partial Denture Wearers, Tighten and Refit Liners"
    assert apply_rules(title) is None


def test_full_denture_set_is_not_forced_irrelevant():
    title = "Silicone Full Denture Set, Tooth Repair Kit, Dentures for Men and Woman, Full Set of Silicone Dentures (1 pc)"
    assert apply_rules(title) is None


def test_base_former_kit_defers_rather_than_guesses():
    title = "Dental Base Former Kit - Soft Silicone Blue Denture Mold - Full-Mouth Standard Dentition Silicone Upper and Lower Jaw Universal Adult Dental Model"
    assert apply_rules(title) is None


# --- known limitation: rules cannot fully disambiguate every AMBIGUOUS case ---

def test_known_limitation_diy_full_denture_kit_collides_with_bead_pattern():
    """B0FGXS3NLX is gold-labeled UNCERTAIN (a DIY full/partial-denture kit),
    but its title also contains 'Fake Teeth' and 'Denture Repair kit', which
    trigger the WRONG_CATEGORY rule. This is a documented, accepted rule
    limitation (see docs/classification_guidelines.md and rules.py's module
    docstring) — surfaced honestly by evaluation.py rather than patched away
    with an ever-growing exception list that would overfit to this one row."""
    title = ("DIY Denture Kit,2 Sets (28 pcs Each) of Different Sizes of False Teeth,"
             "Multi-Color repeatable moldable Material,Complete Fake Teeth Repair Kit,"
             "Partial dentures,Upper/Lower Dentures,Denture Repair kit")
    assert apply_rules(title) == ("IRRELEVANT", "WRONG_CATEGORY")


def test_no_rule_match_defers_to_ai():
    assert apply_rules("Some entirely unrelated dental widget with no known signal") is None


def test_empty_title_defers_rather_than_errors():
    assert apply_rules("") is None
    assert apply_rules(None) is None
