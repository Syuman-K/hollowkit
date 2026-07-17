"""HollowKit headless test.

Run:
  & "C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe" \
      -b --factory-startup --python _test/test_pipeline.py
"""
import sys, os
import bpy, bmesh
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))  # dev/ so `import hollowkit`

import hollowkit
hollowkit.register()

FAIL = []
def check(cond, msg):
    print(("  OK  " if cond else "  FAIL") + " " + msg)
    if not cond:
        FAIL.append(msg)

def clean():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)

def analyze(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = obj.evaluated_get(dg)
    me = ev.to_mesh()
    bm = bmesh.new(); bm.from_mesh(me)
    nonman = sum(1 for e in bm.edges if not e.is_manifold)
    bm.verts.ensure_lookup_table()
    seen=set(); shells=0
    for v in bm.verts:
        if v in seen: continue
        shells+=1; stack=[v]
        while stack:
            w=stack.pop()
            if w in seen: continue
            seen.add(w)
            for e in w.link_edges: stack.append(e.other_vert(w))
    vol = bm.calc_volume()
    bb=[Vector(c) for c in ev.bound_box]
    dim = Vector((max(p.x for p in bb)-min(p.x for p in bb),
                  max(p.y for p in bb)-min(p.y for p in bb),
                  max(p.z for p in bb)-min(p.z for p in bb)))
    nv=len(bm.verts); nf=len(bm.faces)
    bm.free(); ev.to_mesh_clear()
    return dict(v=nv,f=nf,nonman=nonman,shells=shells,vol=vol,dim=dim)

core = hollowkit.core
st = bpy.context.scene.hollowkit

print("\n[1] ノードグループ生成(2段階)")
ngh = core.ensure_hollow_group()
ngd = core.ensure_drill_group()
check(ngh is not None and ngh.bl_idname=='GeometryNodeTree', "中空化グループ生成")
check(ngd is not None and ngd.bl_idname=='GeometryNodeTree', "穴あけグループ生成")
hids = core.input_identifiers(ngh)
dids = core.input_identifiers(ngd)
check(all(k in hids for k in (core.S_WALL, core.S_VOXEL, core.S_SOLID_COLL)),
      "中空化グループの入力 identifier 取得")
check(all(k in dids for k in (core.S_HOLE_DIA, core.S_HOLE_COLL)),
      "穴あけグループの入力 identifier 取得")

print("\n[2] 中空化のみ(20mm 立方体, 壁厚2mm)")
clean()
bpy.ops.mesh.primitive_cube_add(size=20)
cube = bpy.context.active_object
st.scope='SELECTED'; st.use_hollow=True
st.wall_thickness=2.0; st.voxel_mode='MANUAL'; st.voxel_size=0.5
core.apply_to_objects(bpy.context, st, [cube])
r = analyze(cube)
print("   ", r)
check(r['nonman']==0, "中空シェルが水密(非多様体0)")
check(r['shells']==2, "外殻+内空洞=2シェル")
check(abs(r['dim'].x-20)<1e-6, "外形が厳密に維持されている(=20mm)")
check(3500 < r['vol'] < 4300, "シェル体積が概ね壁厚2mm相当(~3904mm^3)")

# 外側ディテール完全保持: 元の8コーナー頂点が座標そのまま存在すること
dg = bpy.context.evaluated_depsgraph_get()
ev = cube.evaluated_get(dg); me = ev.to_mesh()
coords = {tuple(round(c, 5) for c in v.co) for v in me.vertices}
corners = {(x, y, z) for x in (-10.0, 10.0) for y in (-10.0, 10.0)
           for z in (-10.0, 10.0)}
check(corners <= coords, "元の外側頂点が無変更で残っている(ディテール保持)")
ev.to_mesh_clear()

print("\n[3] 中空化 + 穴あけ(マーカー2個)")
clean()
bpy.ops.mesh.primitive_cube_add(size=20)
cube = bpy.context.active_object
st.hole_diameter=3.0
st.hole_len_mode='MANUAL'; st.hole_length=100.0
core.apply_to_objects(bpy.context, st, [cube])
core.add_hole_marker(bpy.context, cube, location=Vector((6,5,-10)),
                     normal=Vector((0,0,-1)))
core.add_hole_marker(bpy.context, cube, location=Vector((10,-5,4)),
                     normal=Vector((1,0,0)))
core.sync_modifier(cube, st)
check(core.count_markers(cube)==2, "穴マーカーが2個")
r = analyze(cube)
print("   ", r)
check(r['nonman']==0, "中空+穴あけ後も水密(非多様体0)")
check(r['vol'] < 3900, "穴のぶん体積が減少")

print("\n[4] ライブ同期(壁厚を厚くすると体積増)")
before = analyze(cube)['vol']
st.wall_thickness=4.0
core.sync_modifier(cube, st)
after = analyze(cube)['vol']
print("   壁厚2mm体積={:.1f} → 4mm体積={:.1f}".format(before, after))
check(after > before, "壁厚を増やすとシェル体積が増える(同期OK)")

print("\n[5] スコープ/自動解像度 + apply operator")
clean()
bpy.ops.mesh.primitive_uv_sphere_add(radius=15)
sph = bpy.context.active_object
st.wall_thickness=2.0; st.voxel_mode='AUTO'; st.detail=96
st.hole_len_mode='AUTO'
res = bpy.ops.hollowkit.apply()
check('FINISHED' in res, "apply オペレーター成功")
check(core.get_hollow_modifier(sph) is not None, "球にモディファイア付与")
check(core.is_frozen(sph), "適用で自動的に固定(段階分け)される")
r = analyze(sph)
print("   ", r)
check(r['nonman']==0 and r['shells']==2, "球の中空化も水密2シェル")

print("\n[6] 二段階の確定(中空化→穴あけ)")
core.bake_hollow_object(bpy.context, sph)
check(core.get_hollow_modifier(sph) is None, "①確定で中空化モディファイアが消える")
check(len(sph.data.vertices) > 100, "①確定後の実メッシュに頂点がある")
shell_vol_bm = bmesh.new(); shell_vol_bm.from_mesh(sph.data)
shell_vol = shell_vol_bm.calc_volume(); shell_vol_bm.free()
core.add_hole_marker(bpy.context, sph, location=Vector((0,0,-15)),
                     normal=Vector((0,0,-1)))
check(core.get_drill_modifier(sph) is not None,
      "穴マーカー追加で穴あけモディファイアが自動付与")
core.bake_drill_object(bpy.context, sph)
check(core.get_drill_modifier(sph) is None, "②確定で穴あけモディファイアが消える")
check(core.count_markers(sph)==0, "②確定で穴マーカーが片付く")
holed_bm = bmesh.new(); holed_bm.from_mesh(sph.data)
holed_vol = holed_bm.calc_volume(); holed_bm.free()
print("   shell={:.1f} → holed={:.1f}".format(shell_vol, holed_vol))
check(holed_vol < shell_vol, "②確定で実際に穴が開いている")

print("\n[7] 解除(clear)")
clean()
bpy.ops.mesh.primitive_cube_add(size=20)
c2 = bpy.context.active_object
core.apply_to_objects(bpy.context, st, [c2])
core.add_hole_marker(bpy.context, c2, location=Vector((0,0,-10)))
core.remove_from_object(c2, remove_markers=True)
check(core.get_hollow_modifier(c2) is None, "解除でモディファイア削除")
check(core.count_markers(c2)==0, "解除でマーカー削除")

print("\n[8] 小空洞(レジン溜まり)の自動削除")
clean()
bpy.ops.mesh.primitive_cube_add(size=20)
big = bpy.context.active_object
bpy.ops.mesh.primitive_cube_add(size=8, location=(40, 0, 0))
small = bpy.context.active_object
bpy.ops.object.select_all(action='DESELECT')
big.select_set(True); small.select_set(True)
bpy.context.view_layer.objects.active = big
bpy.ops.object.join()
obj = bpy.context.active_object

st.scope='SELECTED'; st.use_hollow=True
st.wall_thickness=2.0; st.voxel_mode='MANUAL'; st.voxel_size=0.5

st.cavity_mode='LARGEST'   # 既定: 最大の空洞のみ残す
core.apply_to_objects(bpy.context, st, [obj])
r = analyze(obj)
print("   LARGEST:", r)
# 20mm側の空洞(≈4096mm³)だけが残り、8mm側の空洞(≈64mm³)は削除される
check(r['shells']==3, "最大のみ: 外殻2+空洞1=3シェル")
check(4200 < r['vol'] < 4700, "体積が『大空洞のみ』相当(≈4416mm³)")
check(r['nonman']==0, "最大のみモードでも水密")

st.cavity_mode='THRESHOLD'; st.min_cavity_size=6.0  # 球径6mm(≈113mm³)未満を埋める
core.sync_modifier(obj, st)
rt = analyze(obj)
print("   THRESHOLD 6mm:", rt)
check(rt['shells']==3, "サイズ指定: 小空洞のみ削除で3シェル")

st.cavity_mode='ALL'       # 全空洞を残す
core.sync_modifier(obj, st)
r0 = analyze(obj)
print("   ALL:", r0)
check(r0['shells']==4, "全て残す: 4シェル")
check(r0['vol'] < r['vol'], "小空洞のぶん体積が減る")
st.cavity_mode='LARGEST'

print("\n[9] 軸打ち用の中実柱(軸マーカー)")
clean()
bpy.ops.mesh.primitive_cube_add(size=20)
cube = bpy.context.active_object
st.scope='SELECTED'; st.use_hollow=True
st.wall_thickness=2.0; st.voxel_mode='MANUAL'; st.voxel_size=0.5
st.cavity_mode='LARGEST'
st.solid_diameter=8.0; st.solid_length=15.0
core.apply_to_objects(bpy.context, st, [cube])
base_vol = analyze(cube)['vol']
# 底面中央に軸マーカー(矢印は上=内側へ)。柱は z=-10..+5、空洞は z=-8..8
core.add_solid_marker(bpy.context, cube, location=Vector((0,0,-10)),
                      normal=Vector((0,0,-1)))
core.sync_modifier(cube, st)
r = analyze(cube)
print("   ", r, "base_vol={:.1f}".format(base_vol))
check(core.count_solid_markers(cube)==1, "軸マーカーが1個")
# 柱∩空洞 = 8×8×13 = 832mm³ ぶん中身が増える(四角柱)
gain = r['vol'] - base_vol
check(700 < gain < 950, "柱のぶん体積が増える(≈832mm³, 実測 {:.0f})".format(gain))
check(r['nonman']==0, "柱入りでも水密")
check(r['shells']==2, "柱は空洞壁と一体(2シェルのまま)")

print("\n[10] 中空化キャッシュ(固定)")
st.hole_diameter=3.0
st.hole_len_mode='MANUAL'; st.hole_length=100.0
core.sync_modifier(cube, st)
core.add_hole_marker(bpy.context, cube, location=Vector((6,5,-10)),
                     normal=Vector((0,0,-1)))
core.sync_modifier(cube, st)
vol_live = analyze(cube)['vol']
core.freeze_object(bpy.context, cube)
check(core.is_frozen(cube), "固定状態になっている")
check(bpy.data.objects.get(core.CACHE_PREFIX + cube.name) is not None,
      "キャッシュオブジェクトが存在")
vol_frozen = analyze(cube)['vol']
print("   live={:.1f} frozen={:.1f}".format(vol_live, vol_frozen))
check(abs(vol_frozen - vol_live) < 50, "固定前後で結果が一致")
# 固定中でも穴マーカー追加が反映される(軽い経路)
core.add_hole_marker(bpy.context, cube, location=Vector((-6,-5,-10)),
                     normal=Vector((0,0,-1)))
vol2 = analyze(cube)['vol']
check(vol2 < vol_frozen, "固定中も穴追加が反映(体積減)")
# 固定中でも軸マーカー追加が反映される(柱はキャッシュ後段)
core.add_solid_marker(bpy.context, cube, location=Vector((0,10,0)),
                      normal=Vector((0,1,0)))
vol3 = analyze(cube)['vol']
check(core.is_frozen(cube), "軸マーカー追加後も固定のまま")
check(vol3 > vol2, "固定中も軸柱追加が反映(体積増)")
# 中空化パラメータ変更で自動解除
bpy.context.view_layer.objects.active = cube
st.wall_thickness = 3.0
check(not core.is_frozen(cube), "壁厚変更でキャッシュ自動解除")
st.wall_thickness = 2.0

print("\n[11] 空洞プレビュー")
pv = core.create_preview(bpy.context, cube, st)
check(pv is not None, "プレビュー物体が作成される")
check(core.get_hollow_modifier(cube).show_viewport == False,
      "プレビュー中は本体モディファイア表示OFF")
dg = bpy.context.evaluated_depsgraph_get()
pme = pv.evaluated_get(dg).to_mesh()
pv_verts = len(pme.vertices)
pv.evaluated_get(dg).to_mesh_clear()
print("   preview verts:", pv_verts)
check(pv_verts > 100, "プレビューに空洞ジオメトリがある")
core.remove_preview(cube)
check(core.get_preview(cube) is None, "プレビュー終了で物体が消える")
check(core.get_hollow_modifier(cube).show_viewport == True,
      "終了で本体モディファイア表示が戻る")

print("\n[12] マーカーの矢印方向と柱/穴の方向一致(回転伝搬)")
clean()
bpy.ops.mesh.primitive_cube_add(size=20)
cube = bpy.context.active_object
st.scope='SELECTED'; st.use_hollow=True
st.wall_thickness=2.0; st.voxel_mode='MANUAL'; st.voxel_size=0.5
st.cavity_mode='LARGEST'; st.solid_diameter=8.0; st.solid_length=15.0
core.apply_to_objects(bpy.context, st, [cube])
base_vol = analyze(cube)['vol']

# +X 面に軸マーカー、矢印は -X(内向き) → 柱は -X 方向へ生えるはず
m = core.add_solid_marker(bpy.context, cube, location=Vector((10,0,0)),
                          normal=Vector((1,0,0)))
core.sync_modifier(cube, st)
gain_in = analyze(cube)['vol'] - base_vol
# 矢印を +X(外向き)に反転 → 柱はほぼ外=体積増ほぼ無し
core._aim_z(m, Vector((1,0,0)))
gain_out = analyze(cube)['vol'] - base_vol
print("   pillar: 内向き gain={:.0f} (期待≈832) / 外向き gain={:.0f} (期待≈0)".format(
    gain_in, gain_out))
check(700 < gain_in < 950, "柱が矢印(内向き-X)方向に生える")
check(gain_out < 100, "矢印を外向きにすると柱は生えない(方向追従)")
core.delete_solid_collection(cube)
core.sync_modifier(cube, st)

# 穴: +X 面中央のマーカー。内向き(-X)なら両壁貫通、外向き(+X)なら片壁のみ
st.hole_diameter=3.0
st.hole_len_mode='MANUAL'; st.hole_length=100.0
hm = core.add_hole_marker(bpy.context, cube, location=Vector((10,0,0)),
                          normal=Vector((1,0,0)))   # 矢印 -X 内向き
core.sync_modifier(cube, st)
cut_in = base_vol - analyze(cube)['vol']
core._aim_z(hm, Vector((1,0,0)))                    # 矢印 +X 外向き
cut_out = base_vol - analyze(cube)['vol']
print("   hole: 内向き cut={:.1f} (両壁≈36) / 外向き cut={:.1f} (片壁≈18)".format(
    cut_in, cut_out))
check(cut_in > cut_out + 5, "穴が矢印方向へ掘られる(内向き=両壁貫通)")
check(10 < cut_out < 30, "外向き矢印は手前の壁だけ切る")

print("\n[13] 高密度メッシュの穴あけ(Manifold ソルバー)")
clean()
bpy.ops.mesh.primitive_cube_add(size=20)
cube = bpy.context.active_object
sm = cube.modifiers.new("s", "SUBSURF")
sm.levels = 6; sm.subdivision_type = 'SIMPLE'
bpy.ops.object.modifier_apply(modifier="s")
print("   dense faces:", len(cube.data.polygons))
st.scope='SELECTED'; st.use_hollow=True
st.wall_thickness=2.0; st.voxel_mode='MANUAL'; st.voxel_size=0.5
st.cavity_mode='LARGEST'
st.hole_len_mode='MANUAL'; st.hole_length=100.0; st.hole_diameter=3.0
core.apply_to_objects(bpy.context, st, [cube])
core.freeze_object(bpy.context, cube)
core.add_hole_marker(bpy.context, cube, location=Vector((6,5,-10)),
                     normal=Vector((0,0,-1)))
core.add_hole_marker(bpy.context, cube, location=Vector((-5,4,-10)),
                     normal=Vector((0,0,-1)))
core.sync_modifier(cube, st)
r = analyze(cube)
print("   Manifold:", {k: r[k] for k in ('v','nonman','shells')},
      "vol={:.0f}".format(r['vol']))
check(r['nonman']==0, "高密度でも水密(Manifold, 非多様体0)")
check(r['vol'] < 3930, "穴が実際に開いている")

print("\n[14] 非水密メッシュで穴あけONでも消えない(自動フォールバック)")
clean()
bpy.ops.mesh.primitive_cube_add(size=20)
cube = bpy.context.active_object
bm = bmesh.new(); bm.from_mesh(cube.data)
vs = [bm.verts.new(co) for co in [(-3,-3,0),(3,-3,0),(3,3,0),(-3,3,0)]]
bm.faces.new(vs)     # 内部に浮いた面 = 非多様体(Manifold ソルバーは拒否する)
bm.to_mesh(cube.data); bm.free()
st.use_hollow=True
st.wall_thickness=2.0; st.voxel_mode='MANUAL'; st.voxel_size=0.5
core.apply_to_objects(bpy.context, st, [cube])
r0 = analyze(cube)   # マーカー0個: ブーリアン自体スキップ
print("   markers=0:", {'v': r0['v']}, "vol={:.0f}".format(r0['vol']))
check(r0['v'] > 0, "穴あけON+マーカー0でもメッシュが消えない(スキップ)")
core.add_hole_marker(bpy.context, cube, location=Vector((6,5,-10)),
                     normal=Vector((0,0,-1)))
core.sync_modifier(cube, st)
r1 = analyze(cube)   # Manifold は空を返す → EXACT へ自動フォールバック
print("   markers=1:", {'v': r1['v']}, "vol={:.0f}".format(r1['vol']))
check(r1['v'] > 0, "非水密メッシュでも消えない(EXACT 自動フォールバック)")
check(r1['vol'] < r0['vol'], "フォールバック経由でも穴は開いている")

print("\n[15] 自動の穴長は空洞まで(反対側の壁を貫通しない)")
clean()
bpy.ops.mesh.primitive_cube_add(size=20)
cube = bpy.context.active_object
st.use_hollow=True
st.wall_thickness=2.0; st.voxel_mode='MANUAL'; st.voxel_size=0.5
st.hole_diameter=3.0; st.hole_len_mode='AUTO'
core.apply_to_objects(bpy.context, st, [cube])
base = analyze(cube)['vol']
core.add_hole_marker(bpy.context, cube, location=Vector((6,5,-10)),
                     normal=Vector((0,0,-1)))   # 底面、矢印は上(内向き)
core.sync_modifier(cube, st)
cut = base - analyze(cube)['vol']
print("   auto cut = {:.1f} (片壁のみ≈18 / 両壁貫通なら≈36)".format(cut))
check(10 < cut < 30, "自動長=壁1枚+空洞到達のみ(反対側は無傷)")
st.use_hollow=False
core.sync_modifier(cube, st)
cut_solid = 8000.0 - analyze(cube)['vol']
print("   非中空 auto cut = {:.1f} (全貫通≈180)".format(cut_solid))
check(cut_solid > 150, "非中空時の自動長は全体を貫通")
st.use_hollow=True

print("\n==== RESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAIL: {FAIL}")
hollowkit.unregister()
