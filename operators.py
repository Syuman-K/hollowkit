"""HollowKit のオペレーター(UI とコア処理の橋渡し)。"""

import bpy
from bpy.types import Operator
from bpy_extras import view3d_utils
from mathutils import Vector

from . import core


def _has_input(context):
    st = context.scene.hollowkit
    return bool(core.gather_objects(context, st.scope))


class HOLLOWKIT_OT_apply(Operator):
    bl_idname = "hollowkit.apply"
    bl_label = "中空化を適用 / 更新"
    bl_description = ("対象メッシュに HollowKit の Geometry Nodes モディファイアを"
                     "付与(または更新)する。非破壊なので後からパラメータを"
                     "調整でき、穴マーカーもいつでも追加できる")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _has_input(context)

    def execute(self, context):
        st = context.scene.hollowkit
        objs = core.gather_objects(context, st.scope)
        if not objs:
            self.report({'WARNING'}, "処理対象のメッシュがありません")
            return {'CANCELLED'}
        try:
            done = core.apply_to_objects(context, st, objs)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the user
            self.report({'ERROR'}, "HollowKit 失敗: {}".format(exc))
            return {'CANCELLED'}
        self.report(
            {'INFO'},
            "HollowKit: {} オブジェクトに適用。壁厚 {:.3g}、"
            "穴マーカーを追加して穴を掘れます".format(
                len(done), st.wall_thickness))
        return {'FINISHED'}


class HOLLOWKIT_OT_add_hole(Operator):
    bl_idname = "hollowkit.add_hole"
    bl_label = "穴マーカーを追加"
    bl_description = ("穴(排出/エア抜き)を掘る位置に矢印マーカーを追加する。"
                     "ビューでクリックした表面に置かれ、矢印がモデル内側=掘る"
                     "方向を向く。あとで移動・回転して調整できる")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def _place_from_mouse(self, context, event):
        """マウス下の表面にレイキャストして (位置, 法線) を返す。無ければ None。"""
        region = context.region
        rv3d = context.region_data
        if region is None or rv3d is None:
            return None
        coord = (event.mouse_x - region.x, event.mouse_y - region.y)
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
        depsgraph = context.evaluated_depsgraph_get()
        result, location, normal, index, obj, matrix = context.scene.ray_cast(
            depsgraph, origin, direction)
        if not result:
            return None
        return location, normal

    def invoke(self, context, event):
        obj = context.active_object
        hit = self._place_from_mouse(context, event)
        if hit is not None:
            location, normal = hit
            core.add_hole_marker(context, obj, location=location, normal=normal)
            self.report({'INFO'}, "穴マーカーを表面に追加しました")
            return {'FINISHED'}
        return self.execute(context)

    def execute(self, context):
        obj = context.active_object
        # クリック位置が取れない場合は 3D カーソル位置に、モデル中心へ向けて置く。
        cursor = context.scene.cursor.location.copy()
        center = obj.matrix_world.translation.copy()
        outward = cursor - center
        core.add_hole_marker(
            context, obj, location=cursor,
            normal=outward if outward.length > 1e-6 else Vector((0, 0, 1)))
        self.report({'INFO'}, "穴マーカーを 3D カーソル位置に追加しました")
        return {'FINISHED'}


class HOLLOWKIT_OT_bake(Operator):
    bl_idname = "hollowkit.bake"
    bl_label = "確定(モディファイアを適用)"
    bl_description = ("Geometry Nodes モディファイアを適用してメッシュを確定する。"
                     "以後はパラメータ変更できなくなる。エクスポート前に実行")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _has_input(context)

    def execute(self, context):
        st = context.scene.hollowkit
        objs = [o for o in core.gather_objects(context, st.scope)
                if core.get_modifier(o) is not None]
        if not objs:
            self.report({'WARNING'}, "HollowKit モディファイアを持つ対象がありません")
            return {'CANCELLED'}
        n = 0
        for obj in objs:
            try:
                if core.bake_object(context, obj):
                    core.delete_hole_collection(obj)
                    n += 1
            except Exception as exc:  # noqa: BLE001
                self.report({'ERROR'}, "確定失敗 ({}): {}".format(obj.name, exc))
                return {'CANCELLED'}
        self.report({'INFO'}, "HollowKit: {} オブジェクトを確定しました".format(n))
        return {'FINISHED'}


class HOLLOWKIT_OT_clear(Operator):
    bl_idname = "hollowkit.clear"
    bl_label = "解除"
    bl_description = ("HollowKit のモディファイアと穴マーカーを削除して"
                     "元の状態に戻す")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _has_input(context)

    def execute(self, context):
        st = context.scene.hollowkit
        objs = core.gather_objects(context, st.scope)
        n = 0
        for obj in objs:
            if core.get_modifier(obj) is not None:
                core.remove_from_object(obj, remove_markers=True)
                n += 1
        self.report({'INFO'}, "HollowKit: {} オブジェクトを解除しました".format(n))
        return {'FINISHED'}


CLASSES = (
    HOLLOWKIT_OT_apply,
    HOLLOWKIT_OT_add_hole,
    HOLLOWKIT_OT_bake,
    HOLLOWKIT_OT_clear,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
