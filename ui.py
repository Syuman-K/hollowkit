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

        # --- 軸打ち(中実柱) ---
        box = layout.box()
        box.label(text="軸打ち(中実柱)", icon='EMPTY_AXIS')
        sub = box.column(align=True)
        sub.enabled = st.use_hollow
        sub.prop(st, "solid_diameter")
        sub.prop(st, "solid_length")
        row = sub.row(align=True)
        row.enabled = (obj is not None and obj.type == 'MESH')
        row.operator("hollowkit.add_solid", icon='EMPTY_SINGLE_ARROW')
        if obj is not None and obj.type == 'MESH':
            ns = core.count_solid_markers(obj)
            sub.label(text="軸マーカー: {} 個".format(ns),
                      icon='EMPTY_SINGLE_ARROW')

        # 空洞プレビュー(軸柱・穴をワイヤ表示でライブ確認)
        pv_on = (has_mod and core.get_preview(obj) is not None)
        row = box.row(align=True)
        row.enabled = has_mod
        row.operator("hollowkit.cavity_preview",
                     text="プレビュー終了" if pv_on else "空洞プレビュー",
                     icon='HIDE_OFF', depress=pv_on)
        if pv_on:
            box.label(text="空洞と柱をワイヤ表示中", icon='INFO')

        # --- 穴あけ ---
        box = layout.box()
        box.prop(st, "use_holes")
        sub = box.column(align=True)
        sub.enabled = st.use_holes
        sub.prop(st, "hole_diameter")
        sub.prop(st, "hole_len_mode", expand=True)
        if st.hole_len_mode == 'MANUAL':
            sub.prop(st, "hole_length")
        sub.prop(st, "use_fast_boolean")

        row = sub.row(align=True)
        row.enabled = (obj is not None and obj.type == 'MESH')
        row.operator("hollowkit.add_hole", icon='EMPTY_SINGLE_ARROW')
        if obj is not None and obj.type == 'MESH':
            n = core.count_markers(obj)
            sub.label(text="穴マーカー: {} 個".format(n),
                      icon='EMPTY_SINGLE_ARROW')
            if n == 0:
                sub.label(text="マーカーが無いと穴は開きません", icon='INFO')

        # 中空化キャッシュ(穴調整の軽量化)
        frozen = has_mod and core.is_frozen(obj)
        row = box.row(align=True)
        row.enabled = has_mod
        row.operator("hollowkit.freeze",
                     text="固定を解除(再計算)" if frozen
                     else "調整を軽くする(中空化を固定)",
                     icon='FREEZE', depress=frozen)
        if frozen:
            box.label(text="固定中 — 軸柱・穴あけだけ再計算されます",
                      icon='INFO')
        elif has_mod and st.use_hollow:
            box.label(text="未固定 — 調整が重い場合は『中空化を更新』",
                      icon='ERROR')

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
