"""경계 잘라내기·표본 보간·미확정 보존·배포 결과 일관성 검사."""
import json
import math
from pathlib import Path
import unittest
from build_inventory import cut,inside,length
from export_survey import sample,status
from measure_plan import area,measure
HERE=Path(__file__).parent

class GeometryTest(unittest.TestCase):
    def test_crossing_boundary_without_inside_vertices(self):
        p=[[0,0],[2,0],[2,2],[0,2]]
        self.assertEqual(cut([-1,1],[3,1],p),[([0.,1.],[2.,1.])])
        self.assertEqual(cut([-1,3],[3,3],p),[])
    def test_concave_boundary_does_not_bridge_outside(self):
        p=[[0,0],[3,0],[3,3],[2,3],[2,1],[1,1],[1,3],[0,3]]
        pieces=cut([-1,2],[4,2],p)
        self.assertEqual(len(pieces),2)
        self.assertEqual(pieces,[([0.,2.],[1.,2.]),([2.,2.],[3.,2.])])
    def test_local_distance_and_sample_with_duplicate_points(self):
        path=[[127,37.5],[127,37.5],[127,37.501]]
        self.assertAlmostEqual(length(path),111.32,places=4)
        self.assertAlmostEqual(sample(path,.5)[1],37.5005,places=7)
    def test_area_is_orientation_independent(self):
        p=[[0,0],[10,0],[10,5],[0,5]]
        self.assertEqual(area(p),50)
        self.assertEqual(area(p[::-1]),50)
    def test_status_does_not_turn_unknown_or_area_into_width(self):
        self.assertEqual(status({'tags':{}},[None,None,None]),'미확정')
        self.assertEqual(status({'tags':{}},[[3,5],None,[4,6]]),'부분 판독')
        self.assertEqual(status({'tags':{'area':'yes'}},[None]*3),'면 외곽선')
        self.assertEqual(status({'tags':{}},[[20,None]]*3),'하한 포함')

class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv=json.loads((HERE/'inventory.json').read_text());cls.doc=json.loads((HERE/'survey.json').read_text());cls.rows=cls.doc['segments']
    def test_readings_preserve_ids_and_every_sample(self):
        raw=json.loads((HERE/'visual-readings.json').read_text())
        self.assertEqual(len(self.rows),len(self.inv['segments']))
        self.assertEqual(len({r['id'] for r in self.rows}),len(self.rows))
        for i,r in enumerate(self.rows,1):
            self.assertEqual(raw[str(i)]['id'],r['id'])
            self.assertEqual(len(r['samples']),3)
            self.assertEqual(r['source'],'가정');self.assertIsNone(r['widthM'])
            for s in r['samples']:
                self.assertTrue(127.10<s['lngLat'][0]<127.15 and 37.50<s['lngLat'][1]<37.54)
                w=s['pavingWidthRangeM']
                if w is not None:
                    self.assertGreater(w[0],0)
                    if w[1] is not None:self.assertGreaterEqual(w[1],w[0])
    def test_area_perimeters_not_counted_as_linear_route(self):
        area_rows=[r for r in self.rows if r['tags'].get('area')=='yes']
        self.assertEqual(len(area_rows),2)
        for r in area_rows:self.assertTrue(all(s['pavingWidthRangeM'] is None for s in r['samples']))
        total=sum(r['lengthM'] for r in self.rows)
        coverage=self.doc['coverage']
        self.assertAlmostEqual(coverage['linearLengthM']+coverage['areaPerimeterM'],total,places=4)
        self.assertEqual(coverage['readableSamples'],sum(s['pavingWidthRangeM'] is not None for r in self.rows for s in r['samples']))
    def test_geojson_and_offline_map_keep_all_segments(self):
        gj=json.loads((HERE/'survey.geojson').read_text());self.assertEqual({f['id'] for f in gj['features']},{r['id'] for r in self.rows})
        page=(HERE/'map.html').read_text();self.assertEqual(page.count('<polyline '),len(self.rows))
        self.assertNotIn('<script src=',page);self.assertNotIn('api.vworld.kr/req/wmts',page)
    def test_unknown_plan_gates_stay_unknown(self):
        result=measure(json.loads((HERE/'plan_calipers.json').read_text()))
        self.assertIsNone(result['gates'][1]['allOpenDrawingWidthRangeM'])
        self.assertIsNone(result['gates'][2]['allOpenDrawingWidthRangeM'])
        for g in result['gates']:self.assertIsNone(g['widthM'])
        scales=result['scaleMPerPxByRoom'];self.assertLess(max(scales)/min(scales)-1,.03)

if __name__=='__main__':unittest.main()
