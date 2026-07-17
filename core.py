"""HollowKit のコア: Geometry Nodes ノードグループの生成、モディファイアと
穴マーカーの管理。

処理はすべて 1 つの Geometry Nodes ノードグループ "HollowKit" で行う:

    ジオメトリ ─┬─(中空化 ON)→ SDF化 → 内側オフセット → メッシュ化 →
                │              面反転 → 元ジオメトリと結合 ────────────────┐
                └─(中空化 OFF)────────────────────────────────────────────┴─→ ベース

    ※ 外側は元メッシュをそのまま使う(再メッシュしない)ので、元のディテールは
      一切変わらない。SDF でメッシュ化するのは内側の空洞面だけ。
    ベース ─┬─(穴あけ ON)→ [マーカーコレクション → シリンダーを差し引き] ─┐
            └─(穴あけ OFF)──────────────────────────────────────────────────┴─→ 出力

パラメータ(壁厚・解像度・穴径・穴コレクション等)はグループ入力として
公開し、オブジェクトごとのモディファイア入力値として設定する。
"""

import bpy
from mathutils import Vector

# ---- 定数 -------------------------------------------------------------------

NODE_GROUP = "HollowKit"          # 共有ノードグループ名
NG_VERSION = 3                    # ノードグループ構造のバージョン(変更時に再生成)
MODIFIER = "HollowKit"            # 各オブジェクトに付くモディファイア名
HOLE_COLL_PREFIX = "HK_穴_"       # オブジェクトごとの穴マーカーコレクション接頭辞
HOLE_MARKER_PREFIX = "HK_穴マーカー"

# グループ入力ソケット名(表示名 / 内部の値マッピングキーを兼ねる)
S_GEO = "ジオメトリ"
S_HOLLOW = "中空化"
S_WALL = "壁厚"
S_VOXEL = "解像度(ボクセル)"
S_ADAPT = "滑らかさ"
S_DRILL = "穴あけ"
S_HOLE_DIA = "穴の径"
S_HOLE_LEN = "穴の長さ"
S_HOLE_COLL = "穴コレクション"
S_MIN_CAV = "最小空洞径"
S_ONLY_LARGEST = "最大の空洞のみ"

INPUT_ORDER = (S_GEO, S_HOLLOW, S_WALL, S_VOXEL, S_ADAPT,
               S_ONLY_LARGEST, S_MIN_CAV,
               S_DRILL, S_HOLE_DIA, S_HOLE_LEN, S_HOLE_COLL)


# ---- オブジェクト収集 -------------------------------------------------------

def gather_objects(context, scope):
    """処理対象のメッシュオブジェクトを返す。"""
    if scope == 'ALL':
        pool = context.view_layer.objects
    else:
        pool = context.selected_objects
    return [o for o in pool if o.type == 'MESH']


def max_dimension(obj):
    """オブジェクトのワールド寸法の最大辺を返す(0 は避ける)。"""
    d = obj.dimensions
    return max(d.x, d.y, d.z, 1e-6)


# ---- ノードグループ生成 -----------------------------------------------------

def _new_input(ng, name, socket_type, default=None, min_value=None,
               max_value=None, subtype=None):
    s = ng.interface.new_socket(name, in_out='INPUT', socket_type=socket_type)
    if default is not None:
        s.default_value = default
    if min_value is not None:
        s.min_value = min_value
    if max_value is not None:
        s.max_value = max_value
    if subtype is not None:
        try:
            s.subtype = subtype
        except Exception:
            pass
    return s


def ensure_node_group():
    """共有ノードグループを取得(無い/構造が古ければ再生成)する。"""
    ng = bpy.data.node_groups.get(NODE_GROUP)
    if (ng is not None and ng.bl_idname == 'GeometryNodeTree'
            and ng.get("hk_version") == NG_VERSION):
        return ng
    new = _build_node_group()
    if ng is not None:
        # 旧バージョンのグループを使っているモディファイアを移行して破棄。
        for obj in bpy.data.objects:
            mod = obj.modifiers.get(MODIFIER)
            if mod is not None and mod.type == 'NODES' and mod.node_group == ng:
                mod.node_group = new
        bpy.data.node_groups.remove(ng)
        new.name = NODE_GROUP
    return new


def _build_node_group():
    ng = bpy.data.node_groups.new(NODE_GROUP, "GeometryNodeTree")

    # --- インターフェイス(入出力ソケット) ---
    _new_input(ng, S_GEO, 'NodeSocketGeometry')
    _new_input(ng, S_HOLLOW, 'NodeSocketBool', default=True)
    _new_input(ng, S_WALL, 'NodeSocketFloat', default=2.0, min_value=0.0,
               subtype='DISTANCE')
    _new_input(ng, S_VOXEL, 'NodeSocketFloat', default=0.5, min_value=1e-5,
               subtype='DISTANCE')
    _new_input(ng, S_ADAPT, 'NodeSocketFloat', default=0.0, min_value=0.0,
               max_value=1.0)
    _new_input(ng, S_ONLY_LARGEST, 'NodeSocketBool', default=True)
    _new_input(ng, S_MIN_CAV, 'NodeSocketFloat', default=5.0, min_value=0.0,
               subtype='DISTANCE')
    _new_input(ng, S_DRILL, 'NodeSocketBool', default=True)
    _new_input(ng, S_HOLE_DIA, 'NodeSocketFloat', default=3.0, min_value=0.0,
               subtype='DISTANCE')
    _new_input(ng, S_HOLE_LEN, 'NodeSocketFloat', default=100.0, min_value=0.0,
               subtype='DISTANCE')
    _new_input(ng, S_HOLE_COLL, 'NodeSocketCollection')
    ng.interface.new_socket(S_GEO, in_out='OUTPUT',
                            socket_type='NodeSocketGeometry')

    N = ng.nodes.new
    L = ng.links.new
    gin = N("NodeGroupInput")
    gout = N("NodeGroupOutput")
    gin.location = (-800, 0)
    gout.location = (1400, 0)

    def gi(name):
        return gin.outputs[name]

    # === 中空化パス =========================================================
    sdf = N("GeometryNodeMeshToSDFGrid")
    L(gi(S_GEO), sdf.inputs["Mesh"])
    L(gi(S_VOXEL), sdf.inputs["Voxel Size"])

    # バンド幅(ボクセル数) = 壁厚 / ボクセル + 余裕。int ソケットへ暗黙変換。
    band = N("ShaderNodeMath"); band.operation = 'DIVIDE'
    L(gi(S_WALL), band.inputs[0])
    L(gi(S_VOXEL), band.inputs[1])
    band_pad = N("ShaderNodeMath"); band_pad.operation = 'ADD'
    band_pad.inputs[1].default_value = 6.0
    L(band.outputs[0], band_pad.inputs[0])
    L(band_pad.outputs[0], sdf.inputs["Band Width"])

    # 内側へ壁厚ぶんオフセット(距離は負)。
    neg = N("ShaderNodeMath"); neg.operation = 'MULTIPLY'
    neg.inputs[1].default_value = -1.0
    L(gi(S_WALL), neg.inputs[0])
    offset = N("GeometryNodeSDFGridOffset")
    L(sdf.outputs["SDF Grid"], offset.inputs["Grid"])
    L(neg.outputs[0], offset.inputs["Distance"])

    # 空洞面だけメッシュ化する。外側は元メッシュをそのまま使うので、
    # 元のディテールは一切変わらない。
    g2m = N("GeometryNodeGridToMesh")
    g2m.inputs["Threshold"].default_value = 0.0
    L(offset.outputs["Grid"], g2m.inputs["Grid"])
    L(gi(S_ADAPT), g2m.inputs["Adaptivity"])

    # --- 小さい空洞(レジン溜まり)の除去 ---------------------------------
    # 空洞メッシュをアイランド(独立した閉空間)ごとに符号付き体積で測り、
    # 「最小空洞径」の球の体積より小さいアイランドを削除する(=その部分は
    # 中実のまま残る)。三角形ごとの体積寄与 dot(p0, p1×p2)/6 をアイランド
    # 単位で合算する。
    tri = N("GeometryNodeTriangulate")
    L(g2m.outputs["Mesh"], tri.inputs["Mesh"])

    face_idx = N("GeometryNodeInputIndex")
    positions = N("GeometryNodeInputPosition")
    corner_p = []
    for sort in range(3):
        cof = N("GeometryNodeCornersOfFace")
        cof.inputs["Sort Index"].default_value = sort
        L(face_idx.outputs["Index"], cof.inputs["Face Index"])
        voc = N("GeometryNodeVertexOfCorner")
        L(cof.outputs["Corner Index"], voc.inputs["Corner Index"])
        smp = N("GeometryNodeSampleIndex")
        smp.data_type = 'FLOAT_VECTOR'
        smp.domain = 'POINT'
        L(tri.outputs["Mesh"], smp.inputs["Geometry"])
        L(positions.outputs["Position"], smp.inputs["Value"])
        L(voc.outputs["Vertex Index"], smp.inputs["Index"])
        corner_p.append(smp.outputs["Value"])

    cross = N("ShaderNodeVectorMath"); cross.operation = 'CROSS_PRODUCT'
    L(corner_p[1], cross.inputs[0])
    L(corner_p[2], cross.inputs[1])
    dot = N("ShaderNodeVectorMath"); dot.operation = 'DOT_PRODUCT'
    L(corner_p[0], dot.inputs[0])
    L(cross.outputs["Vector"], dot.inputs[1])
    sixth = N("ShaderNodeMath"); sixth.operation = 'DIVIDE'
    sixth.inputs[1].default_value = 6.0
    L(dot.outputs["Value"], sixth.inputs[0])

    island = N("GeometryNodeInputMeshIsland")
    acc = N("GeometryNodeAccumulateField")
    acc.data_type = 'FLOAT'
    acc.domain = 'FACE'
    L(sixth.outputs[0], acc.inputs["Value"])
    L(island.outputs["Island Index"], acc.inputs["Group ID"])
    vol_abs = N("ShaderNodeMath"); vol_abs.operation = 'ABSOLUTE'
    L(acc.outputs["Total"], vol_abs.inputs[0])

    # しきい値体積 = π/6 × 径³ (径の球の体積)。
    d2 = N("ShaderNodeMath"); d2.operation = 'MULTIPLY'
    L(gi(S_MIN_CAV), d2.inputs[0])
    L(gi(S_MIN_CAV), d2.inputs[1])
    d3 = N("ShaderNodeMath"); d3.operation = 'MULTIPLY'
    L(d2.outputs[0], d3.inputs[0])
    L(gi(S_MIN_CAV), d3.inputs[1])
    thr = N("ShaderNodeMath"); thr.operation = 'MULTIPLY'
    thr.inputs[1].default_value = 0.5235988  # π/6
    L(d3.outputs[0], thr.inputs[0])

    small = N("FunctionNodeCompare")
    small.data_type = 'FLOAT'
    small.operation = 'LESS_THAN'
    L(vol_abs.outputs[0], small.inputs[0])
    L(thr.outputs[0], small.inputs[1])

    # 「最大の空洞のみ」モード: 全アイランド中の最大体積を求め、それ未満の
    # アイランドをすべて削除する(同一アイランド内は Accumulate の Total が
    # 全く同じ値になるため、最大アイランドだけが等値=残る)。
    stat = N("GeometryNodeAttributeStatistic")
    stat.data_type = 'FLOAT'
    stat.domain = 'FACE'
    L(tri.outputs["Mesh"], stat.inputs["Geometry"])
    L(vol_abs.outputs[0], stat.inputs["Attribute"])
    not_largest = N("FunctionNodeCompare")
    not_largest.data_type = 'FLOAT'
    not_largest.operation = 'LESS_THAN'
    L(vol_abs.outputs[0], not_largest.inputs[0])
    L(stat.outputs["Max"], not_largest.inputs[1])

    sel = N("GeometryNodeSwitch"); sel.input_type = 'BOOLEAN'
    L(gi(S_ONLY_LARGEST), sel.inputs["Switch"])
    L(small.outputs["Result"], sel.inputs["False"])
    L(not_largest.outputs["Result"], sel.inputs["True"])

    del_small = N("GeometryNodeDeleteGeometry")
    del_small.domain = 'FACE'
    del_small.mode = 'ALL'
    L(tri.outputs["Mesh"], del_small.inputs["Geometry"])
    L(sel.outputs["Output"], del_small.inputs["Selection"])

    # 面を反転して空洞(内向き法線)にし、元ジオメトリと結合して殻にする。
    flip = N("GeometryNodeFlipFaces")
    L(del_small.outputs["Geometry"], flip.inputs["Mesh"])
    join = N("GeometryNodeJoinGeometry")
    L(gi(S_GEO), join.inputs[0])
    L(flip.outputs["Mesh"], join.inputs[0])

    sw_hollow = N("GeometryNodeSwitch"); sw_hollow.input_type = 'GEOMETRY'
    L(gi(S_HOLLOW), sw_hollow.inputs["Switch"])
    L(gi(S_GEO), sw_hollow.inputs["False"])
    L(join.outputs[0], sw_hollow.inputs["True"])
    base = sw_hollow.outputs["Output"]

    # === 穴あけパス =========================================================
    colinfo = N("GeometryNodeCollectionInfo")
    colinfo.transform_space = 'RELATIVE'
    colinfo.inputs["Separate Children"].default_value = True
    colinfo.inputs["Reset Children"].default_value = False
    L(gi(S_HOLE_COLL), colinfo.inputs["Collection"])

    i2p = N("GeometryNodeInstancesToPoints")
    L(colinfo.outputs["Instances"], i2p.inputs["Instances"])

    radius = N("ShaderNodeMath"); radius.operation = 'MULTIPLY'
    radius.inputs[1].default_value = 0.5
    L(gi(S_HOLE_DIA), radius.inputs[0])
    cyl = N("GeometryNodeMeshCylinder")
    cyl.inputs["Vertices"].default_value = 32
    L(radius.outputs[0], cyl.inputs["Radius"])
    L(gi(S_HOLE_LEN), cyl.inputs["Depth"])

    inst_rot = N("GeometryNodeInputInstanceRotation")
    iop = N("GeometryNodeInstanceOnPoints")
    L(i2p.outputs["Points"], iop.inputs["Points"])
    L(cyl.outputs["Mesh"], iop.inputs["Instance"])
    L(inst_rot.outputs["Rotation"], iop.inputs["Rotation"])

    realize = N("GeometryNodeRealizeInstances")
    L(iop.outputs["Instances"], realize.inputs["Geometry"])

    drill = N("GeometryNodeMeshBoolean")
    drill.operation = 'DIFFERENCE'
    if "Self Intersection" in drill.inputs:
        drill.inputs["Self Intersection"].default_value = True
    L(base, drill.inputs["Mesh 1"])
    L(realize.outputs["Geometry"], drill.inputs["Mesh 2"])

    sw_drill = N("GeometryNodeSwitch"); sw_drill.input_type = 'GEOMETRY'
    L(gi(S_DRILL), sw_drill.inputs["Switch"])
    L(base, sw_drill.inputs["False"])
    L(drill.outputs["Mesh"], sw_drill.inputs["True"])

    L(sw_drill.outputs["Output"], gout.inputs[0])
    ng["hk_version"] = NG_VERSION
    return ng


def input_identifiers(ng):
    """入力ソケット名 → identifier のマッピングを返す(モディファイア値設定用)。"""
    mapping = {}
    for item in ng.interface.items_tree:
        if getattr(item, 'in_out', None) == 'INPUT' and item.item_type == 'SOCKET':
            mapping[item.name] = item.identifier
    return mapping


# ---- モディファイア管理 -----------------------------------------------------

def get_modifier(obj):
    mod = obj.modifiers.get(MODIFIER)
    if mod is not None and mod.type == 'NODES':
        return mod
    return None


def ensure_modifier(obj, ng):
    mod = get_modifier(obj)
    if mod is None:
        mod = obj.modifiers.new(MODIFIER, 'NODES')
    mod.node_group = ng
    return mod


def sync_modifier(obj, st):
    """シーン設定の値をオブジェクトのモディファイア入力へ書き込む。"""
    mod = get_modifier(obj)
    if mod is None:
        return False
    ng = mod.node_group or ensure_node_group()
    ids = input_identifiers(ng)
    voxel = resolve_voxel(st, obj)
    depth = resolve_depth(st, obj)
    values = {
        S_HOLLOW: st.use_hollow,
        S_WALL: st.wall_thickness,
        S_VOXEL: voxel,
        S_ADAPT: st.adaptivity,
        S_ONLY_LARGEST: st.cavity_mode == 'LARGEST',
        S_MIN_CAV: (st.min_cavity_size
                    if st.cavity_mode == 'THRESHOLD' else 0.0),
        S_DRILL: st.use_holes,
        S_HOLE_DIA: st.hole_diameter,
        S_HOLE_LEN: depth,
        S_HOLE_COLL: get_hole_collection(obj, create=False),
    }
    for name, val in values.items():
        ident = ids.get(name)
        if ident is None:
            continue
        try:
            mod[ident] = val
        except Exception:
            pass
    mod.show_viewport = True
    obj.update_tag()
    return True


def resolve_voxel(st, obj):
    """解像度(ボクセルサイズ)を解決する。自動なら最大寸法 / ディテール。"""
    if st.voxel_mode == 'AUTO':
        return max_dimension(obj) / max(st.detail, 1)
    return st.voxel_size


def resolve_depth(st, obj):
    """穴の長さを解決する。自動なら最大寸法の 2 倍(確実に貫通)。"""
    if st.hole_len_mode == 'AUTO':
        return max_dimension(obj) * 2.0
    return st.hole_length


def apply_to_objects(context, st, objs):
    """対象オブジェクトへノードグループのモディファイアを付与し値を同期する。"""
    ng = ensure_node_group()
    done = []
    for obj in objs:
        ensure_modifier(obj, ng)
        get_hole_collection(obj, create=True)   # 穴コレクションを用意
        sync_modifier(obj, st)
        done.append(obj)
    return done


def sync_active(context):
    """アクティブオブジェクトにモディファイアがあれば設定を反映(ライブ更新)。"""
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        return
    if get_modifier(obj) is None:
        return
    sync_modifier(obj, context.scene.hollowkit)


def remove_from_object(obj, remove_markers=True):
    mod = get_modifier(obj)
    if mod is not None:
        obj.modifiers.remove(mod)
    if remove_markers:
        delete_hole_collection(obj)


def bake_object(context, obj):
    """モディファイアを適用してメッシュを確定する。"""
    mod = get_modifier(obj)
    if mod is None:
        return False
    ctx = context.copy()
    ctx["object"] = obj
    with context.temp_override(object=obj, active_object=obj,
                               selected_objects=[obj]):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return True


# ---- 穴マーカー(Empty)管理 -------------------------------------------------

def _hole_coll_name(obj):
    return HOLE_COLL_PREFIX + obj.name


def get_hole_collection(obj, create=True):
    """オブジェクト専用の穴マーカーコレクションを返す。"""
    name = _hole_coll_name(obj)
    coll = bpy.data.collections.get(name)
    if coll is None and create:
        coll = bpy.data.collections.new(name)
        # シーンに紐付ける(表示のため)。親コレクションが無ければシーン直下。
        parent = bpy.data.collections.get("HollowKit")
        if parent is None:
            parent = bpy.data.collections.new("HollowKit")
            bpy.context.scene.collection.children.link(parent)
        if name not in [c.name for c in parent.children]:
            parent.children.link(coll)
    return coll


def delete_hole_collection(obj):
    coll = bpy.data.collections.get(_hole_coll_name(obj))
    if coll is None:
        return
    for o in list(coll.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.collections.remove(coll)


def add_hole_marker(context, obj, location=None, normal=None):
    """穴マーカー(矢印 Empty)を追加し、穴コレクションへ入れて対象に親付けする。

    矢印(+Z)が穴を掘る向きになる。既定では下向き(排出穴向き)。
    """
    coll = get_hole_collection(obj, create=True)
    size = max_dimension(obj) * 0.15
    empty = bpy.data.objects.new(HOLE_MARKER_PREFIX, None)
    empty.empty_display_type = 'SINGLE_ARROW'
    empty.empty_display_size = size
    coll.objects.link(empty)

    if location is None:
        location = obj.matrix_world.translation.copy()
    empty.matrix_world.translation = location

    # 向き: 法線があればその逆向き(内側=掘る方向)、無ければ下向き。
    if normal is not None and normal.length > 1e-6:
        _aim_z(empty, -normal.normalized())
    else:
        _aim_z(empty, Vector((0.0, 0.0, -1.0)))

    # 親付け(オフセットを保ったまま)。
    empty.parent = obj
    empty.matrix_parent_inverse = obj.matrix_world.inverted()
    return empty


def _aim_z(obj, direction):
    """オブジェクトの +Z が direction を向くよう回転を設定する。"""
    z = Vector((0.0, 0.0, 1.0))
    quat = z.rotation_difference(direction.normalized())
    obj.rotation_euler = quat.to_euler()


def count_markers(obj):
    coll = bpy.data.collections.get(_hole_coll_name(obj))
    return len(coll.objects) if coll else 0


def has_modifier(context):
    obj = context.active_object
    return obj is not None and obj.type == 'MESH' and get_modifier(obj) is not None
