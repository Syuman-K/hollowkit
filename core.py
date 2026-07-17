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
NG_VERSION = 8                    # ノードグループ構造のバージョン(変更時に再生成)
MODIFIER = "HollowKit"            # 各オブジェクトに付くモディファイア名
HOLE_COLL_PREFIX = "HK_穴_"       # オブジェクトごとの穴マーカーコレクション接頭辞
HOLE_MARKER_PREFIX = "HK_穴マーカー"
SOLID_COLL_PREFIX = "HK_軸_"      # 軸打ち用・中実柱マーカーコレクション接頭辞
SOLID_MARKER_PREFIX = "HK_軸マーカー"
CACHE_PREFIX = "HK_cache_"        # 中空化キャッシュ(隠しメッシュ)接頭辞
PREVIEW_PREFIX = "HK_プレビュー_"  # 空洞プレビュー物体の接頭辞

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
S_SOLID_COLL = "軸コレクション"
S_SOLID_DIA = "軸柱の径"
S_SOLID_LEN = "軸柱の長さ"
S_USE_CACHE = "キャッシュ使用"
S_CACHE_OBJ = "キャッシュ"
S_PREVIEW = "プレビュー出力"     # 内部用: 空洞(+柱)だけを出力(プレビュー物体)
S_CAPTURE = "キャッシュ取得"     # 内部用: 柱を引く前の生空洞を出力(freeze 用)

INPUT_ORDER = (S_GEO, S_HOLLOW, S_WALL, S_VOXEL, S_ADAPT,
               S_ONLY_LARGEST, S_MIN_CAV,
               S_SOLID_COLL, S_SOLID_DIA, S_SOLID_LEN,
               S_USE_CACHE, S_CACHE_OBJ, S_PREVIEW, S_CAPTURE,
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
    _new_input(ng, S_SOLID_COLL, 'NodeSocketCollection')
    _new_input(ng, S_SOLID_DIA, 'NodeSocketFloat', default=10.0, min_value=0.0,
               subtype='DISTANCE')
    _new_input(ng, S_SOLID_LEN, 'NodeSocketFloat', default=20.0, min_value=0.0,
               subtype='DISTANCE')
    _new_input(ng, S_USE_CACHE, 'NodeSocketBool', default=False)
    _new_input(ng, S_CACHE_OBJ, 'NodeSocketObject')
    _new_input(ng, S_PREVIEW, 'NodeSocketBool', default=False)
    _new_input(ng, S_CAPTURE, 'NodeSocketBool', default=False)
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

    def sampled_rotation(instances_out):
        """マーカーインスタンスの回転を取り出すフィールドを作る。

        Instances to Points は回転属性を運ばないため、Instance on Points の
        Rotation に Input Instance Rotation を直結すると常に単位回転になる
        (v0.3 までのバグ)。ポイント index = 元インスタンス index を利用し、
        Sample Index(QUATERNION, INSTANCE ドメイン)で元から回転を引く。
        """
        rot = N("GeometryNodeInputInstanceRotation")
        idx = N("GeometryNodeInputIndex")
        si = N("GeometryNodeSampleIndex")
        si.data_type = 'QUATERNION'
        si.domain = 'INSTANCE'
        L(instances_out, si.inputs["Geometry"])
        L(rot.outputs["Rotation"], si.inputs["Value"])
        L(idx.outputs["Index"], si.inputs["Index"])
        return si.outputs["Value"]

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

    # --- 軸打ち用の中実柱(軸マーカー) -----------------------------------
    # 軸マーカーの位置・向きに柱(シリンダー)を作り、空洞から差し引く。
    # 柱の部分は中実のまま残るので、ダボ面からの軸打ちができる。
    # 柱はマーカー位置から矢印(+Z)方向へ「軸柱の長さ」ぶん伸びる。
    s_col = N("GeometryNodeCollectionInfo")
    s_col.transform_space = 'RELATIVE'
    s_col.inputs["Separate Children"].default_value = True
    s_col.inputs["Reset Children"].default_value = False
    L(gi(S_SOLID_COLL), s_col.inputs["Collection"])
    s_i2p = N("GeometryNodeInstancesToPoints")
    L(s_col.outputs["Instances"], s_i2p.inputs["Instances"])

    # 柱も直方体(8頂点)でブーリアンを軽くする。幅=軸柱の幅、奥行=長さ。
    s_size = N("ShaderNodeCombineXYZ")
    L(gi(S_SOLID_DIA), s_size.inputs["X"])
    L(gi(S_SOLID_DIA), s_size.inputs["Y"])
    L(gi(S_SOLID_LEN), s_size.inputs["Z"])
    s_cyl = N("GeometryNodeMeshCube")
    L(s_size.outputs["Vector"], s_cyl.inputs["Size"])
    # 直方体は中心原点なので +Z へ半分ずらし、マーカー位置から先へ伸ばす。
    s_half = N("ShaderNodeMath"); s_half.operation = 'DIVIDE'
    s_half.inputs[1].default_value = 2.0
    L(gi(S_SOLID_LEN), s_half.inputs[0])
    s_off = N("ShaderNodeCombineXYZ")
    L(s_half.outputs[0], s_off.inputs["Z"])
    s_tr = N("GeometryNodeTransform")
    L(s_cyl.outputs["Mesh"], s_tr.inputs["Geometry"])
    L(s_off.outputs["Vector"], s_tr.inputs["Translation"])

    s_iop = N("GeometryNodeInstanceOnPoints")
    L(s_i2p.outputs["Points"], s_iop.inputs["Points"])
    L(s_tr.outputs["Geometry"], s_iop.inputs["Instance"])
    L(sampled_rotation(s_col.outputs["Instances"]), s_iop.inputs["Rotation"])
    s_real = N("GeometryNodeRealizeInstances")
    L(s_iop.outputs["Instances"], s_real.inputs["Geometry"])

    # --- 空洞キャッシュ切替 ---------------------------------------------
    # 「キャッシュ使用」時は、確定済みの生空洞(柱を引く前・隠しメッシュ)を
    # 使い、重い SDF 計算を飛ばす。柱・穴あけはキャッシュの後段なので、
    # 軸マーカー・穴マーカーとも軽いブーリアンだけで再計算される。
    cache_info = N("GeometryNodeObjectInfo")
    cache_info.transform_space = 'RELATIVE'
    L(gi(S_CACHE_OBJ), cache_info.inputs["Object"])
    sw_cav = N("GeometryNodeSwitch"); sw_cav.input_type = 'GEOMETRY'
    L(gi(S_USE_CACHE), sw_cav.inputs["Switch"])
    L(del_small.outputs["Geometry"], sw_cav.inputs["False"])
    L(cache_info.outputs["Geometry"], sw_cav.inputs["True"])

    # 空洞(GridToMesh 由来)と直方体は常に水密なので Manifold ソルバーで
    # 高速・水密にくり抜ける。軸マーカーが 0 個ならブーリアン自体を
    # スキップする(空のカッターでソルバーを走らせない)。
    s_bool = N("GeometryNodeMeshBoolean")
    s_bool.operation = 'DIFFERENCE'
    if hasattr(s_bool, "solver"):
        s_bool.solver = 'MANIFOLD'
    L(sw_cav.outputs["Output"], s_bool.inputs["Mesh 1"])
    L(s_real.outputs["Geometry"], s_bool.inputs["Mesh 2"])

    sp_size = N("GeometryNodeAttributeDomainSize")
    sp_size.component = 'POINTCLOUD'
    L(s_i2p.outputs["Points"], sp_size.inputs["Geometry"])
    has_pillars = N("FunctionNodeCompare")
    has_pillars.data_type = 'INT'
    has_pillars.operation = 'GREATER_THAN'
    L(sp_size.outputs["Point Count"], has_pillars.inputs[2])
    has_pillars.inputs[3].default_value = 0
    sw_pil = N("GeometryNodeSwitch"); sw_pil.input_type = 'GEOMETRY'
    L(has_pillars.outputs["Result"], sw_pil.inputs["Switch"])
    L(sw_cav.outputs["Output"], sw_pil.inputs["False"])
    L(s_bool.outputs["Mesh"], sw_pil.inputs["True"])

    # 面を反転して空洞(内向き法線)にし、元ジオメトリと結合して殻にする。
    flip = N("GeometryNodeFlipFaces")
    L(sw_pil.outputs["Output"], flip.inputs["Mesh"])
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

    # ドリルは直方体(8頂点)。円柱よりブーリアンが大幅に軽く、排出穴の
    # 用途では形状の差は問題にならない。幅=穴の幅、奥行=穴の長さ。
    h_size = N("ShaderNodeCombineXYZ")
    L(gi(S_HOLE_DIA), h_size.inputs["X"])
    L(gi(S_HOLE_DIA), h_size.inputs["Y"])
    L(gi(S_HOLE_LEN), h_size.inputs["Z"])
    cyl = N("GeometryNodeMeshCube")
    L(h_size.outputs["Vector"], cyl.inputs["Size"])

    # ドリルはマーカー位置から矢印(+Z)方向へ掘る。マーカーが表面ぴったりに
    # あっても壁を確実に切れるよう、後方に穴幅ぶんだけはみ出させる。
    h_half = N("ShaderNodeMath"); h_half.operation = 'DIVIDE'
    h_half.inputs[1].default_value = 2.0
    L(gi(S_HOLE_LEN), h_half.inputs[0])
    h_back = N("ShaderNodeMath"); h_back.operation = 'SUBTRACT'
    L(h_half.outputs[0], h_back.inputs[0])
    L(gi(S_HOLE_DIA), h_back.inputs[1])
    h_off = N("ShaderNodeCombineXYZ")
    L(h_back.outputs[0], h_off.inputs["Z"])
    h_tr = N("GeometryNodeTransform")
    L(cyl.outputs["Mesh"], h_tr.inputs["Geometry"])
    L(h_off.outputs["Vector"], h_tr.inputs["Translation"])

    iop = N("GeometryNodeInstanceOnPoints")
    L(i2p.outputs["Points"], iop.inputs["Points"])
    L(h_tr.outputs["Geometry"], iop.inputs["Instance"])
    L(sampled_rotation(colinfo.outputs["Instances"]), iop.inputs["Rotation"])

    realize = N("GeometryNodeRealizeInstances")
    L(iop.outputs["Instances"], realize.inputs["Geometry"])

    # ドリルはまず Manifold ソルバー(高速・高密度メッシュでも水密)で切り、
    # 結果が空(=対象が水密でなく Manifold が入力を拒否)なら自動で EXACT に
    # フォールバックする。Switch は選ばれた側しか評価しないので、成功時に
    # EXACT のコストは掛からない。
    drill_fast = N("GeometryNodeMeshBoolean")
    drill_fast.operation = 'DIFFERENCE'
    if hasattr(drill_fast, "solver"):
        drill_fast.solver = 'MANIFOLD'
    L(base, drill_fast.inputs["Mesh 1"])
    L(realize.outputs["Geometry"], drill_fast.inputs["Mesh 2"])

    drill_exact = N("GeometryNodeMeshBoolean")
    drill_exact.operation = 'DIFFERENCE'
    if "Self Intersection" in drill_exact.inputs:
        drill_exact.inputs["Self Intersection"].default_value = True
    L(base, drill_exact.inputs["Mesh 1"])
    L(realize.outputs["Geometry"], drill_exact.inputs["Mesh 2"])

    fast_size = N("GeometryNodeAttributeDomainSize")
    fast_size.component = 'MESH'
    L(drill_fast.outputs["Mesh"], fast_size.inputs["Geometry"])
    fast_failed = N("FunctionNodeCompare")
    fast_failed.data_type = 'INT'
    fast_failed.operation = 'EQUAL'
    L(fast_size.outputs["Point Count"], fast_failed.inputs[2])
    fast_failed.inputs[3].default_value = 0
    sw_fallback = N("GeometryNodeSwitch"); sw_fallback.input_type = 'GEOMETRY'
    L(fast_failed.outputs["Result"], sw_fallback.inputs["Switch"])
    L(drill_fast.outputs["Mesh"], sw_fallback.inputs["False"])
    L(drill_exact.outputs["Mesh"], sw_fallback.inputs["True"])

    # 穴マーカーが 0 個ならブーリアン自体をスキップする。
    hp_size = N("GeometryNodeAttributeDomainSize")
    hp_size.component = 'POINTCLOUD'
    L(i2p.outputs["Points"], hp_size.inputs["Geometry"])
    has_holes = N("FunctionNodeCompare")
    has_holes.data_type = 'INT'
    has_holes.operation = 'GREATER_THAN'
    L(hp_size.outputs["Point Count"], has_holes.inputs[2])
    has_holes.inputs[3].default_value = 0
    drill_on = N("FunctionNodeBooleanMath"); drill_on.operation = 'AND'
    L(gi(S_DRILL), drill_on.inputs[0])
    L(has_holes.outputs["Result"], drill_on.inputs[1])

    sw_drill = N("GeometryNodeSwitch"); sw_drill.input_type = 'GEOMETRY'
    L(drill_on.outputs["Boolean"], sw_drill.inputs["Switch"])
    L(base, sw_drill.inputs["False"])
    L(sw_fallback.outputs["Output"], sw_drill.inputs["True"])

    # --- 出力切替(内部用) -----------------------------------------------
    # プレビュー出力: 空洞(+柱)だけを出す(プレビュー物体のワイヤ表示用)。
    # キャッシュ取得: 柱を引く前の生空洞を出す(freeze がこれを保存する)。
    sw_prev = N("GeometryNodeSwitch"); sw_prev.input_type = 'GEOMETRY'
    L(gi(S_PREVIEW), sw_prev.inputs["Switch"])
    L(sw_drill.outputs["Output"], sw_prev.inputs["False"])
    L(sw_pil.outputs["Output"], sw_prev.inputs["True"])
    sw_cap = N("GeometryNodeSwitch"); sw_cap.input_type = 'GEOMETRY'
    L(gi(S_CAPTURE), sw_cap.inputs["Switch"])
    L(sw_prev.outputs["Output"], sw_cap.inputs["False"])
    L(del_small.outputs["Geometry"], sw_cap.inputs["True"])

    L(sw_cap.outputs["Output"], gout.inputs[0])
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


def _build_values(st, obj):
    """シーン設定からモディファイア入力値の辞書を作る。"""
    return {
        S_HOLLOW: st.use_hollow,
        S_WALL: st.wall_thickness,
        S_VOXEL: resolve_voxel(st, obj),
        S_ADAPT: st.adaptivity,
        S_ONLY_LARGEST: st.cavity_mode == 'LARGEST',
        S_MIN_CAV: (st.min_cavity_size
                    if st.cavity_mode == 'THRESHOLD' else 0.0),
        S_SOLID_COLL: get_solid_collection(obj, create=False),
        S_SOLID_DIA: st.solid_diameter,
        S_SOLID_LEN: st.solid_length,
        S_DRILL: st.use_holes,
        S_HOLE_DIA: st.hole_diameter,
        S_HOLE_LEN: resolve_depth(st, obj),
        S_HOLE_COLL: get_hole_collection(obj, create=False),
    }


def _write_values(mod, ids, values):
    for name, val in values.items():
        ident = ids.get(name)
        if ident is None:
            continue
        try:
            mod[ident] = val
        except Exception:
            pass


def sync_modifier(obj, st):
    """シーン設定の値をオブジェクト(とプレビュー物体)のモディファイアへ
    書き込む。S_USE_CACHE / S_CACHE_OBJ は freeze/unfreeze が管理する。"""
    mod = get_modifier(obj)
    if mod is None:
        return False
    ng = mod.node_group or ensure_node_group()
    ids = input_identifiers(ng)
    values = _build_values(st, obj)
    _write_values(mod, ids, values)

    # 空洞プレビュー中は本体の再計算を止め、プレビュー物体だけ更新する。
    pv = get_preview(obj)
    mod.show_viewport = (pv is None)
    if pv is not None:
        pmod = pv.modifiers.get(MODIFIER)
        if pmod is not None and pmod.type == 'NODES' \
                and pmod.node_group is not None:
            pids = input_identifiers(pmod.node_group)
            _write_values(pmod, pids, values)
            try:
                pmod[pids[S_PREVIEW]] = True
                pmod[pids[S_USE_CACHE]] = mod[ids[S_USE_CACHE]]
                pmod[pids[S_CACHE_OBJ]] = mod[ids[S_CACHE_OBJ]]
            except Exception:
                pass
        pv.update_tag()
    obj.update_tag()
    return True


def resolve_voxel(st, obj):
    """解像度(ボクセルサイズ)を解決する。自動なら最大寸法 / ディテール。"""
    if st.voxel_mode == 'AUTO':
        return max_dimension(obj) / max(st.detail, 1)
    return st.voxel_size


def resolve_depth(st, obj):
    """穴の長さを解決する。

    自動のとき: 中空化中は「壁を貫いて空洞に届くだけ」の長さ(壁厚×2 +
    穴幅×2)にし、反対側の壁や途中の形状まで貫通させない。斜め刺しでも
    届くようマージンを含む。中空化しない場合のみ全体を貫通させる。
    """
    if st.hole_len_mode == 'AUTO':
        if st.use_hollow:
            return st.wall_thickness * 2.0 + st.hole_diameter * 2.0
        return max_dimension(obj) * 2.0
    return st.hole_length


def apply_to_objects(context, st, objs):
    """対象オブジェクトへノードグループのモディファイアを付与し値を同期する。"""
    ng = ensure_node_group()
    done = []
    for obj in objs:
        ensure_modifier(obj, ng)
        get_hole_collection(obj, create=True)   # 穴コレクションを用意
        get_solid_collection(obj, create=True)  # 軸コレクションを用意
        unfreeze_object(obj)                    # 更新時はキャッシュを作り直し
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
    remove_preview(obj)
    mod = get_modifier(obj)
    if mod is not None:
        obj.modifiers.remove(mod)
    delete_cache(obj)
    if remove_markers:
        delete_hole_collection(obj)
        delete_solid_collection(obj)


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

def _root_collection():
    """HollowKit の管理物(マーカー/キャッシュ/プレビュー)用の親コレクション。"""
    parent = bpy.data.collections.get("HollowKit")
    if parent is None:
        parent = bpy.data.collections.new("HollowKit")
        bpy.context.scene.collection.children.link(parent)
    return parent


def _marker_collection(obj, prefix, create=True):
    """オブジェクト専用のマーカーコレクションを返す。"""
    name = prefix + obj.name
    coll = bpy.data.collections.get(name)
    if coll is None and create:
        coll = bpy.data.collections.new(name)
        parent = _root_collection()
        if name not in [c.name for c in parent.children]:
            parent.children.link(coll)
    return coll


def get_hole_collection(obj, create=True):
    return _marker_collection(obj, HOLE_COLL_PREFIX, create)


def get_solid_collection(obj, create=True):
    return _marker_collection(obj, SOLID_COLL_PREFIX, create)


def _delete_marker_collection(obj, prefix):
    coll = bpy.data.collections.get(prefix + obj.name)
    if coll is None:
        return
    for o in list(coll.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.collections.remove(coll)


def delete_hole_collection(obj):
    _delete_marker_collection(obj, HOLE_COLL_PREFIX)


def delete_solid_collection(obj):
    _delete_marker_collection(obj, SOLID_COLL_PREFIX)


def _add_marker(context, obj, coll, name, location, normal):
    """矢印 Empty マーカーを追加し、コレクションへ入れて対象に親付けする。

    矢印(+Z)がモデル内側=掘る/柱を伸ばす向きになる。
    """
    size = max_dimension(obj) * 0.15
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_type = 'SINGLE_ARROW'
    empty.empty_display_size = size
    coll.objects.link(empty)

    if location is None:
        location = obj.matrix_world.translation.copy()
    empty.matrix_world.translation = location

    # 向き: 法線があればその逆向き(内側)、無ければ下向き。
    if normal is not None and normal.length > 1e-6:
        _aim_z(empty, -normal.normalized())
    else:
        _aim_z(empty, Vector((0.0, 0.0, -1.0)))

    # 親付け(オフセットを保ったまま)。
    empty.parent = obj
    empty.matrix_parent_inverse = obj.matrix_world.inverted()
    return empty


def add_hole_marker(context, obj, location=None, normal=None):
    """穴(排出/エア抜き)マーカーを追加する。"""
    coll = get_hole_collection(obj, create=True)
    return _add_marker(context, obj, coll, HOLE_MARKER_PREFIX, location, normal)


def add_solid_marker(context, obj, location=None, normal=None):
    """軸打ち用の中実柱マーカーを追加する。柱は矢印方向へ伸びる。

    柱の差し引きはキャッシュの後段なので、固定中でもライブに反映される。
    """
    coll = get_solid_collection(obj, create=True)
    return _add_marker(context, obj, coll, SOLID_MARKER_PREFIX, location, normal)


def _aim_z(obj, direction):
    """オブジェクトの +Z が direction を向くよう回転を設定する。"""
    z = Vector((0.0, 0.0, 1.0))
    quat = z.rotation_difference(direction.normalized())
    obj.rotation_euler = quat.to_euler()


def count_markers(obj):
    coll = bpy.data.collections.get(HOLE_COLL_PREFIX + obj.name)
    return len(coll.objects) if coll else 0


def count_solid_markers(obj):
    coll = bpy.data.collections.get(SOLID_COLL_PREFIX + obj.name)
    return len(coll.objects) if coll else 0


# ---- 中空化キャッシュ(穴調整の軽量化) --------------------------------------

def _cache_name(obj):
    return CACHE_PREFIX + obj.name


def is_frozen(obj):
    """中空化キャッシュが有効かどうか。"""
    mod = get_modifier(obj)
    if mod is None or mod.node_group is None:
        return False
    ids = input_identifiers(mod.node_group)
    ident = ids.get(S_USE_CACHE)
    if ident is None:
        return False
    try:
        return bool(mod[ident])
    except Exception:
        return False


def _preview_mod(obj):
    pv = get_preview(obj)
    if pv is None:
        return None
    pmod = pv.modifiers.get(MODIFIER)
    if pmod is not None and pmod.type == 'NODES' \
            and pmod.node_group is not None:
        return pmod
    return None


def _set_cache_inputs(obj, use, cache):
    """本体とプレビュー両方のモディファイアへキャッシュ状態を書き込む。"""
    for m in (get_modifier(obj), _preview_mod(obj)):
        if m is None or m.node_group is None:
            continue
        ids = input_identifiers(m.node_group)
        try:
            m[ids[S_USE_CACHE]] = use
            m[ids[S_CACHE_OBJ]] = cache
        except Exception:
            pass


def freeze_object(context, obj):
    """生空洞(柱を引く前)を隠しメッシュへ確定し、以後の軸柱・穴あけは
    軽いブーリアンだけで再計算する。マーカーのドラッグが軽くなる。"""
    mod = get_modifier(obj)
    if mod is None or mod.node_group is None:
        return False
    ids = input_identifiers(mod.node_group)

    # キャッシュ取得モードで評価し、柱を引く前の生空洞を取り出す。
    prev_show = mod.show_viewport
    mod.show_viewport = True
    mod[ids[S_CAPTURE]] = True
    mod[ids[S_USE_CACHE]] = False
    obj.update_tag()
    dg = context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = bpy.data.meshes.new_from_object(ev, depsgraph=dg)
    mod[ids[S_CAPTURE]] = False
    mod.show_viewport = prev_show

    # キャッシュオブジェクト(隠しメッシュ)を作成/更新する。
    name = _cache_name(obj)
    cache = bpy.data.objects.get(name)
    if cache is None:
        cache = bpy.data.objects.new(name, me)
        _root_collection().objects.link(cache)
    else:
        old = cache.data
        cache.data = me
        if old is not None and old.users == 0:
            bpy.data.meshes.remove(old)
    me.name = name
    # 対象に追従させ、モディファイア座標系(RELATIVE)で一致させる。
    cache.parent = obj
    cache.matrix_parent_inverse.identity()
    cache.matrix_basis.identity()
    cache.hide_viewport = True
    cache.hide_render = True

    _set_cache_inputs(obj, True, cache)
    obj.update_tag()
    return True


def unfreeze_object(obj):
    """中空化キャッシュを解除し、ライブ計算に戻す。"""
    _set_cache_inputs(obj, False, None)
    delete_cache(obj)
    obj.update_tag()


def delete_cache(obj):
    cache = bpy.data.objects.get(_cache_name(obj))
    if cache is not None:
        me = cache.data
        bpy.data.objects.remove(cache, do_unlink=True)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)


def clear_all_caches():
    """全オブジェクトのキャッシュを解除する(中空化パラメータ変更時)。"""
    for obj in bpy.data.objects:
        if obj.type == 'MESH' and is_frozen(obj):
            unfreeze_object(obj)


# ---- 空洞プレビュー(軸柱をライブ確認) --------------------------------------

def get_preview(obj):
    return bpy.data.objects.get(PREVIEW_PREFIX + obj.name)


def create_preview(context, obj, st):
    """空洞(+軸柱)をワイヤフレームで重ね表示するプレビュー物体を作る。

    本体モディファイアのビューポート表示を切り、調整中の再計算を
    プレビュー(空洞側)だけにする。マーカーを動かすと即座に反映される。
    """
    mod = get_modifier(obj)
    if mod is None:
        return None
    remove_preview(obj)   # 作り直し

    name = PREVIEW_PREFIX + obj.name
    # 本体とメッシュデータを共有する(=ノード入力が元ジオメトリになる)。
    pv = bpy.data.objects.new(name, obj.data)
    _root_collection().objects.link(pv)
    pv.parent = obj
    pv.matrix_parent_inverse.identity()
    pv.matrix_basis.identity()
    pv.display_type = 'WIRE'
    pv.show_in_front = True
    pv.hide_render = True
    pv.hide_select = True

    pmod = pv.modifiers.new(MODIFIER, 'NODES')
    pmod.node_group = mod.node_group or ensure_node_group()
    sync_modifier(obj, st)   # 値の書込み+S_PREVIEW/キャッシュ同期+本体表示OFF
    return pv


def remove_preview(obj):
    """プレビュー物体を削除し、本体モディファイアの表示を戻す。"""
    pv = get_preview(obj)
    if pv is not None:
        me = pv.data
        bpy.data.objects.remove(pv, do_unlink=True)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)
    mod = get_modifier(obj)
    if mod is not None:
        mod.show_viewport = True


def has_modifier(context):
    obj = context.active_object
    return obj is not None and obj.type == 'MESH' and get_modifier(obj) is not None
