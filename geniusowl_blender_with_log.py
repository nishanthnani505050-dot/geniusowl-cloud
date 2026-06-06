# =================================================================
# GeniusOwl Enclosure — Blender Python Script (WITH LOG / PERCH)
# =================================================================
# Uses the perched-owl model (Owl_with_log.stl). Adds:
#   - Hollow interior (2.5 mm walls — applied to owl AND log)
#   - 5×5 speaker grille on the chest
#   - OLED rectangular window on the belly
#   - Ring of 10 mic holes around the head (omnidirectional pickup)
#   - Push-button hole on top of the head
#   - USB-C cutout at the back
#   - Large back access panel for installing electronics
#
# HOW TO USE:
#   1. Make sure Owl_with_log.stl is at the path in the CONFIG block.
#   2. Open Blender. Switch to the "Scripting" workspace (top tab).
#   3. Click "New" to make a new text block, paste this entire file.
#   4. Press ▶ Run Script (or Alt+P).
#   5. Watch the 3D viewport build the owl piece by piece.
#   6. Output STL is saved to the OUTPUT_PATH below.
#
# IF CUTS APPEAR ON THE WRONG SIDE OF THE OWL:
#   The model's "front" direction is best-guessed below. If the
#   speaker grille / OLED end up on the BACK after running, change
#       FRONT_Y_SIGN = -1
#   to
#       FRONT_Y_SIGN = 1
#   and rerun. That single number flips front/back everywhere.
# =================================================================

import bpy
import math
from mathutils import Vector

# =================================================================
# CONFIG — edit these to taste
# =================================================================

# --- File paths ---
OWL_INPUT_PATH = r"C:\Users\Vamshi Manni\OneDrive\Desktop\GeniusOwl.ai\files\Owl_with_log.stl"
OUTPUT_PATH    = r"C:\Users\Vamshi Manni\OneDrive\Desktop\GeniusOwl.ai\files\GeniusOwl_v1_with_log.stl"

# Which Y direction is "front" of the owl. If cuts appear backwards, flip sign.
FRONT_Y_SIGN = -1     # try -1 first; if speaker ends up on back, change to +1

# --- Shell ---
WALL_THICKNESS = 2.5      # mm — wall thickness

# --- Speaker grille (5x5 pattern on chest) ---
SPEAKER_CENTER_X = 0
SPEAKER_CENTER_Z = 95     # mid-chest height
SPEAKER_GRID_COLS = 5
SPEAKER_GRID_ROWS = 5
SPEAKER_HOLE_DIA = 3.5
SPEAKER_HOLE_SPACING = 7

# --- OLED window on belly ---
OLED_CENTER_X = 0
OLED_CENTER_Z = 65        # lower body — belly
OLED_WIDTH    = 28
OLED_HEIGHT   = 16

# --- Mic holes (ring around head) ---
MIC_RING_HEIGHT = 128     # upper head
MIC_RING_RADIUS = 22      # how far out from the center axis
MIC_HOLE_COUNT  = 10      # holes around the ring
MIC_HOLE_DIA    = 2.5

# --- Push-to-talk button on top of head ---
BUTTON_CENTER_X = 0
BUTTON_CENTER_Y = 0
BUTTON_CENTER_Z = 148
BUTTON_DIA      = 7

# --- USB-C cutout on back ---
USB_C_CENTER_X = 0
USB_C_CENTER_Z = 50       # above the log
USB_C_WIDTH    = 12
USB_C_HEIGHT   = 6

# --- Back access panel (for installing electronics) ---
ACCESS_CENTER_X = 0
ACCESS_CENTER_Z = 80      # body back, well above the log
ACCESS_WIDTH    = 60
ACCESS_HEIGHT   = 70


# =================================================================
# DERIVED VALUES (don't edit)
# =================================================================
FRONT_Y     = -30 * FRONT_Y_SIGN
BACK_Y      =  25 * FRONT_Y_SIGN


# =================================================================
# HELPERS
# =================================================================

def clear_scene():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials):
        for item in list(block):
            if item.users == 0:
                block.remove(item)


def import_stl(path):
    try:
        bpy.ops.wm.stl_import(filepath=path)   # Blender 4.x / 5.x
    except AttributeError:
        bpy.ops.import_mesh.stl(filepath=path)  # Blender 3.x
    return bpy.context.selected_objects[0]


def export_stl(obj, path):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    try:
        bpy.ops.wm.stl_export(filepath=path, export_selected_objects=True)
    except AttributeError:
        bpy.ops.export_mesh.stl(filepath=path, use_selection=True)


def make_active(obj):
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def clean_mesh(obj):
    make_active(obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.remove_doubles(threshold=0.001)
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')


def boolean_difference(target, cutter):
    make_active(target)
    mod = target.modifiers.new(name="Cut", type='BOOLEAN')
    mod.operation = 'DIFFERENCE'
    mod.solver = 'FAST'
    mod.object = cutter
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(cutter, do_unlink=True)


def cube_cutter(name, location, size):
    bpy.ops.mesh.primitive_cube_add(location=location)
    c = bpy.context.active_object
    c.name = name
    c.scale = (size[0] / 2, size[1] / 2, size[2] / 2)
    bpy.ops.object.transform_apply(scale=True)
    return c


def cylinder_cutter_along_y(name, location, radius, depth):
    bpy.ops.mesh.primitive_cylinder_add(
        location=location, radius=radius, depth=depth, vertices=32
    )
    c = bpy.context.active_object
    c.name = name
    c.rotation_euler[0] = math.radians(90)
    bpy.ops.object.transform_apply(rotation=True)
    return c


def cylinder_cutter_along_z(name, location, radius, depth):
    bpy.ops.mesh.primitive_cylinder_add(
        location=location, radius=radius, depth=depth, vertices=32
    )
    c = bpy.context.active_object
    c.name = name
    return c


def cylinder_cutter_radial(name, location, angle_rad, radius, depth):
    """Cylinder pointing radially outward from the Z axis at angle_rad."""
    bpy.ops.mesh.primitive_cylinder_add(
        location=location, radius=radius, depth=depth, vertices=24
    )
    c = bpy.context.active_object
    c.name = name
    c.rotation_euler = (math.radians(90), 0, angle_rad)
    bpy.ops.object.transform_apply(rotation=True)
    return c


# =================================================================
# MAIN PIPELINE
# =================================================================

print("=" * 60)
print("GeniusOwl Enclosure Generator — WITH LOG")
print("=" * 60)

# 1. Clean slate
clear_scene()

# 2. Import the perched owl
print(f"Importing: {OWL_INPUT_PATH}")
owl = import_stl(OWL_INPUT_PATH)
owl.name = "GeniusOwl"
print(f"   Source dimensions: {tuple(round(d,1) for d in owl.dimensions)} mm")

# 3. Center on XY. (No rotation needed — file is already Z-up. Base already
#    rests on Z=0 because the model creator placed the log at Z=0.)
bbox_world = [owl.matrix_world @ Vector(c) for c in owl.bound_box]
min_x = min(v.x for v in bbox_world); max_x = max(v.x for v in bbox_world)
min_y = min(v.y for v in bbox_world); max_y = max(v.y for v in bbox_world)
min_z = min(v.z for v in bbox_world)
owl.location.x -= (min_x + max_x) / 2
owl.location.y -= (min_y + max_y) / 2
owl.location.z -= min_z
bpy.ops.object.transform_apply(location=True)
print(f"   Standing dimensions: {tuple(round(d,1) for d in owl.dimensions)} mm")
print(f"   Front of owl is at Y = {FRONT_Y} (FRONT_Y_SIGN = {FRONT_Y_SIGN})")

# 4. Clean the mesh (removes tiny stray fragments and dupe vertices)
clean_mesh(owl)

# 5. Hollow the body (also hollows the log — that's fine, reduces material cost)
print(f"Hollowing — {WALL_THICKNESS} mm wall thickness")
solidify = owl.modifiers.new(name="Hollow", type='SOLIDIFY')
solidify.thickness = -WALL_THICKNESS
solidify.offset = 1
solidify.use_rim = True
make_active(owl)
bpy.ops.object.modifier_apply(modifier=solidify.name)

# 6. Speaker grille (5x5 forward-facing holes on chest)
print("Cutting speaker grille (5x5)...")
for col in range(SPEAKER_GRID_COLS):
    for row in range(SPEAKER_GRID_ROWS):
        x = (col - (SPEAKER_GRID_COLS - 1) / 2) * SPEAKER_HOLE_SPACING + SPEAKER_CENTER_X
        z = (row - (SPEAKER_GRID_ROWS - 1) / 2) * SPEAKER_HOLE_SPACING + SPEAKER_CENTER_Z
        c = cylinder_cutter_along_y(
            f"sp_{col}_{row}",
            (x, FRONT_Y, z),
            SPEAKER_HOLE_DIA / 2,
            20,
        )
        boolean_difference(owl, c)

# 7. OLED window on belly
print("Cutting OLED window...")
c = cube_cutter(
    "oled_window",
    (OLED_CENTER_X, FRONT_Y, OLED_CENTER_Z),
    (OLED_WIDTH, 20, OLED_HEIGHT),
)
boolean_difference(owl, c)

# 8. Ring of mic holes around the head
print(f"Cutting {MIC_HOLE_COUNT} mic holes around head perimeter...")
for i in range(MIC_HOLE_COUNT):
    angle = (i / MIC_HOLE_COUNT) * 2 * math.pi
    x = math.sin(angle) * MIC_RING_RADIUS
    y = math.cos(angle) * MIC_RING_RADIUS
    c = cylinder_cutter_radial(
        f"mic_{i}",
        (x, y, MIC_RING_HEIGHT),
        angle,
        MIC_HOLE_DIA / 2,
        30,
    )
    boolean_difference(owl, c)

# 9. Button hole on top of head
print("Cutting button hole...")
c = cylinder_cutter_along_z(
    "button",
    (BUTTON_CENTER_X, BUTTON_CENTER_Y, BUTTON_CENTER_Z),
    BUTTON_DIA / 2,
    20,
)
boolean_difference(owl, c)

# 10. USB-C slot on back
print("Cutting USB-C slot...")
c = cube_cutter(
    "usb_c",
    (USB_C_CENTER_X, BACK_Y, USB_C_CENTER_Z),
    (USB_C_WIDTH, 20, USB_C_HEIGHT),
)
boolean_difference(owl, c)

# 11. Back access panel
print("Cutting back access panel...")
c = cube_cutter(
    "access_panel",
    (ACCESS_CENTER_X, BACK_Y, ACCESS_CENTER_Z),
    (ACCESS_WIDTH, 20, ACCESS_HEIGHT),
)
boolean_difference(owl, c)

# 12. Export
print(f"Exporting: {OUTPUT_PATH}")
export_stl(owl, OUTPUT_PATH)

print("=" * 60)
print(f"DONE.  Final dimensions: {tuple(round(d,1) for d in owl.dimensions)} mm")
print(f"File:  {OUTPUT_PATH}")
print("=" * 60)
