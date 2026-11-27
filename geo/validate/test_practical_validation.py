"""시간 경계·누락/0 구분·동일 표본 비교 검증."""
import csv
import gzip
from pathlib import Path
import tempfile
import unittest
from practical_validation import baseline,metrics,parking_counts,score_model

class ValidationTests(unittest.TestCase):
    def test_current_month_and_future_counts_cannot_change_baseline(self):
        counts={('2025-01-06','s',17):10,('2025-01-13','s',17):30,('2025-02-03','s',17):9999,('2025-03-03','s',17):9999}
        self.assertEqual(baseline(counts,('2025-02-10','s',17))[:2],(20,2))
        self.assertEqual(baseline(counts,('2025-01-01','s',17)),None)
    def test_weekday_fallback_is_past_site_hour_only(self):
        counts={('2025-01-06','s',17):10,('2025-01-06','other',17):999,('2025-01-06','s',18):999}
        self.assertEqual(baseline(counts,('2025-02-04','s',17))[0],10)
    def test_metrics_keep_units_and_bias(self):
        r=metrics([10,20],[8,24]);self.assertEqual(r['mae'],3);self.assertAlmostEqual(r['wape_pct'],20)
        self.assertAlmostEqual(r['bias_pct'],100*2/30)
        with self.assertRaises(ValueError):metrics([1,2],[1])
    def test_missing_model_is_not_zero_error(self):
        self.assertEqual(score_model(None,[])['status'],'missing_model_ledger')
        self.assertEqual(score_model(None,[])['paired'],[])
    def ledger(self,root,rows):
        p=root/'pred.csv'
        with p.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['date','site','hour','train_end','phase','prediction']);w.writeheader();w.writerows(rows)
        return p
    def test_forecast_trained_in_target_month_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=self.ledger(Path(d),[dict(date='2025-07-10',site='s',hour=17,train_end='2025-07-01',phase='model',prediction=10)])
            with self.assertRaises(ValueError):score_model(p,[])
    def test_model_and_baseline_use_identical_matched_rows(self):
        actual=[dict(date='2025-07-10',site='s',hour=17,observed=10,baseline=20),dict(date='2025-07-11',site='s',hour=17,observed=999,baseline=0)]
        with tempfile.TemporaryDirectory() as d:
            p=self.ledger(Path(d),[dict(date='2025-07-10',site='s',hour=17,train_end='2025-06-30',phase='model',prediction=12)])
            result=score_model(p,actual)['paired'][0]
            self.assertEqual(result['model']['mae'],2);self.assertEqual(result['same_rows_baseline']['mae'],10)
            self.assertEqual(result['mae_improvement_pct'],80)
    def test_duplicate_or_nonfinite_forecasts_rejected(self):
        row=dict(date='2025-07-10',site='s',hour=17,train_end='2025-06-30',phase='model',prediction=10)
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):score_model(self.ledger(Path(d),[row,row]),[])
            with self.assertRaises(ValueError):score_model(self.ledger(Path(d),[{**row,'prediction':'nan'}]),[])
    def test_reversed_missing_dates_and_partial_december_are_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'parking.csv.gz'
            with gzip.open(p,'wt',encoding='utf-8',newline='') as f:
                w=csv.DictWriter(f,fieldnames=['entry_gate','exit_gate','entry_time','exit_time']);w.writeheader()
                for day in ['2025-06-01','2025-12-01','2025-07-31']:
                    for gate in ['북2문','동2문','남2문']:
                        w.writerow(dict(entry_gate=gate,exit_gate=gate,entry_time=day+' 08:00:00',exit_time=day+' 17:00:00'))
                w.writerow(dict(entry_gate='북2문',exit_gate='북2문',entry_time='2025-06-02 19:00:00',exit_time='2025-06-02 17:00:00'))
            counts,meta=parking_counts(p)
            self.assertEqual(meta['quality']['reversed_rows'],1)
            self.assertEqual({k[0] for k in counts},{'2025-06-01'})
            self.assertEqual(counts[('2025-06-01','car-north2',18)],0)
            self.assertNotIn(('2025-06-02','car-north2',18),counts)

if __name__=='__main__':unittest.main()
