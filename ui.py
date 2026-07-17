"""N パネル UI (View3D > サイドバー > HollowKit)。"""

import bpy
from bpy.types import Panel

from . import core

_CATEGORY = "HollowKit"


class HOLLOWKIT_PT_main(Panel):
    bl_idname = "HOLLOWKIT_PT_main"
    bl_label = "HollowKit"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = _CATEGORY

    def draw(self, context):
        st = context.scene.hollowkit
        layout = self.layout
        obj = context.active_object
        has_mod = core.has_modifier(context)

        layout.prop(st, "scope", expand=True)

        # 実行ボタン(最上部固定)
        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("hollowkit.apply",
                     text="中空化を更新" if has_mod else "中空化を適用",
                     icon='MOD_SOLIDIFY')

        # --- 中空化 ---
        box = layout.box()
        box.prop(st, "use_hollow")
        sub = box.column(align=True)
        sub.enabled = st.use_hollow
        sub.prop(st, "wall_thickness")
        sub.prop(st, "voxel_mode", expand=True)
        if st.voxel_mode == 'AUTO':
            sub.prop(st, "detail")
            if obj is not None and obj.type == 'MESH':
                sub.label(text="ボクセル: {:.4g}".format(
                    core.resolve_voxel(st, obj)), icon='MOD_REMESH')
        else:
            sub.prop(st, "voxel_size")
        sub.prop(st, "adaptivity", slider=True)
        sub.prop(st, "cavity_mode", text="空洞")
        if st.cavity_mode == 'THRESHOLD':
            sub.prop(st, "min_cavity_size")

        # --- 穴あけ ---
        box = layout.box()
        box.prop(st, "use_holes")
        sub = box.column(align=True)
        sub.enabled = st.use_holes
        sub.prop(st, "hole_diameter")
        sub.prop(st, "hole_len_mode", expand=True)
        if st.hole_len_mode == 'MANUAL':
            sub.prop(st, "hole_length")

        row = sub.row(align=True)
        row.enabled = (obj is not None and obj.type == 'MESH')
        row.operator("hollowkit.add_hole", icon='EMPTY_SINGLE_ARROW')
        if obj is not None and obj.type == 'MESH':
            n = core.count_markers(obj)
            sub.label(text="穴マーカー: {} 個".format(n),
                      icon='EMPTY_SINGLE_ARROW')
            if n == 0:
                sub.label(text="マーカーが無いと穴は開きません", icon='INFO')

        # --- 確定 / 解除 ---
        box = layout.box()
        box.label(text="仕上げ", icon='CHECKMARK')
        row = box.row(align=True)
        row.operator("hollowkit.bake", icon='CHECKMARK')
        row.operator("hollowkit.clear", text="", icon='X')

        if has_mod:
            box.label(text="適用中 — 値の変更は即反映されます", icon='INFO')


CLASSES = (
    HOLLOWKIT_PT_main,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
