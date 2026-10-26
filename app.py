"""
Geogeolevel v4.2 - Исправлен парсер для схемы BFFB (ЗППЗ)
"""

import io
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, Set

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# ============================================================
# МОДЕЛИ ДАННЫХ
# ============================================================

@dataclass
class RawReading:
    """Один отсчет по рейке"""
    point: str
    reading: float
    distance: float
    reading_type: str  # 'Rb' или 'Rf'


@dataclass
class RawMeasurement:
    """Превышение между двумя точками"""
    back_point: str
    fore_point: str
    back_reading: float
    fore_reading: float
    back_dist: float
    fore_dist: float


@dataclass
class Station:
    """Станция с усредненным превышением"""
    station_id: int
    back_point: str
    fore_point: str
    dh_mean: float
    distance: float
    measurements: List[RawMeasurement] = field(default_factory=list)


@dataclass
class LevelingRun:
    """Нивелирный ход"""
    run_id: int
    stations: List[Station]
    start_point: str
    end_point: str


@dataclass
class AdjustedPoint:
    """Уравненная точка"""
    point_id: str
    h_original: Optional[float]
    h_adjusted: float
    correction: float
    distance_from_start: float


# ============================================================
# ПАРСЕР TRIMBLE DINI BFFB
# ============================================================

class TrimbleDiniBFFBParser:
    """Парсер для формата Trimble DiNi Start-Line BFFB (ЗППЗ)"""
    
    def __init__(self, raw_bytes: bytes):
        self.raw_bytes = raw_bytes

    def parse(self) -> Tuple[str, List[RawMeasurement], Dict[str, float]]:
        text = self._decode_text()
        lines = text.splitlines()
        
        readings = []  # Все отсчеты по порядку
        points_with_heights = {}
        job_name = "Unknown Job"
        
        # Извлекаем имя файла
        for line in lines[:5]:
            if 'TO' in line and '.dat' in line.lower():
                match = re.search(r'TO\s+(\S+\.dat)', line, re.IGNORECASE)
                if match:
                    job_name = match.group(1).replace('.dat', '')
                break
        
        # Собираем все отсчеты Rb и Rf
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Отметки точек (Z)
            z_match = re.search(r'\|Z\s+([+-]?\d+\.?\d*)\s*m\s*\|', line)
            if z_match:
                point_match = re.search(r'\|KD1\s+(\S+)\s+', line)
                if point_match:
                    point_name = point_match.group(1)
                    height = float(z_match.group(1))
                    points_with_heights[point_name] = height
                continue
            
            # Отсчет по задней рейке (Rb)
            rb_match = re.search(r'\|Rb\s+([+-]?\d+\.?\d*)\s*m\s*\|HD\s+([+-]?\d+\.?\d*)\s*m\s*\|', line)
            if rb_match:
                point_match = re.search(r'\|KD1\s+(\S+)\s+', line)
                if point_match:
                    readings.append(RawReading(
                        point=point_match.group(1),
                        reading=float(rb_match.group(1)),
                        distance=float(rb_match.group(2)),
                        reading_type='Rb'
                    ))
                continue
            
            # Отсчет по передней рейке (Rf)
            rf_match = re.search(r'\|Rf\s+([+-]?\d+\.?\d*)\s*m\s*\|HD\s+([+-]?\d+\.?\d*)\s*m\s*\|', line)
            if rf_match:
                point_match = re.search(r'\|KD1\s+(\S+)\s+', line)
                if point_match:
                    readings.append(RawReading(
                        point=point_match.group(1),
                        reading=float(rf_match.group(1)),
                        distance=float(rf_match.group(2)),
                        reading_type='Rf'
                    ))
        
        # Группируем отсчеты по станциям (схема BFFB: Rb, Rf, Rf, Rb)
        measurements = self._group_readings_into_measurements(readings)
        
        return job_name, measurements, points_with_heights

    def _group_readings_into_measurements(self, readings: List[RawReading]) -> List[RawMeasurement]:
        """
        Группирует отсчеты по станциям схемы BFFB.
        Ожидает последовательность: Rb(A), Rf(B), Rf(B), Rb(A)
        Создает два измерения и возвращает их.
        """
        measurements = []
        i = 0
        
        while i < len(readings):
            # Ищем начало станции (Rb)
            if readings[i].reading_type == 'Rb':
                rb1 = readings[i]
                
                # Ищем первый Rf
                if i + 1 < len(readings) and readings[i + 1].reading_type == 'Rf':
                    rf1 = readings[i + 1]
                    
                    # Создаем первое измерение
                    measurements.append(RawMeasurement(
                        back_point=rb1.point,
                        fore_point=rf1.point,
                        back_reading=rb1.reading,
                        fore_reading=rf1.reading,
                        back_dist=rb1.distance,
                        fore_dist=rf1.distance
                    ))
                    
                    # Проверяем, есть ли повторные отсчеты (Rf, Rb)
                    if i + 2 < len(readings) and readings[i + 2].reading_type == 'Rf':
                        rf2 = readings[i + 2]
                        
                        # Второй Rf должен быть на той же точке
                        if rf2.point == rf1.point and i + 3 < len(readings) and readings[i + 3].reading_type == 'Rb':
                            rb2 = readings[i + 3]
                            
                            # Создаем второе измерение (контрольное)
                            measurements.append(RawMeasurement(
                                back_point=rb2.point,
                                fore_point=rf2.point,
                                back_reading=rb2.reading,
                                fore_reading=rf2.reading,
                                back_dist=rb2.distance,
                                fore_dist=rf2.distance
                            ))
                            
                            i += 4  # Пропускаем все 4 отсчета
                            continue
                        else:
                            i += 2  # Только первая пара
                            continue
                    else:
                        i += 2  # Только первая пара
                        continue
                else:
                    i += 1
                    continue
            else:
                i += 1
        
        return measurements

    def _decode_text(self) -> str:
        for enc in ['utf-8', 'windows-1251', 'cp1251', 'latin-1']:
            try:
                return self.raw_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.raw_bytes.decode('latin-1', errors='ignore')


# ============================================================
# АНАЛИЗАТОР СТРУКТУРЫ ХОДОВ
# ============================================================

class RunAnalyzer:
    """Анализирует измерения и определяет структуру ходов"""
    
    def __init__(self, measurements: List[RawMeasurement], points_with_heights: Dict[str, float]):
        self.measurements = measurements
        self.points_with_heights = points_with_heights
    
    def group_into_stations(self) -> List[Station]:
        """Группирует измерения по станциям и усредняет"""
        station_groups = defaultdict(list)
        
        for m in self.measurements:
            key = (m.back_point, m.fore_point)
            station_groups[key].append(m)
        
        stations = []
        for idx, ((back_pt, fore_pt), meas_list) in enumerate(station_groups.items(), 1):
            dh_values = [m.back_reading - m.fore_reading for m in meas_list]
            dist_values = [m.back_dist + m.fore_dist for m in meas_list]
            
            dh_mean = sum(dh_values) / len(dh_values)
            dist_mean = sum(dist_values) / len(dist_values)
            
            stations.append(Station(
                station_id=idx,
                back_point=back_pt,
                fore_point=fore_pt,
                dh_mean=dh_mean,
                distance=dist_mean,
                measurements=meas_list
            ))
        
        return stations
    
    def identify_runs(self, stations: List[Station]) -> List[LevelingRun]:
        """Определяет отдельные ходы на основе связности"""
        if not stations:
            return []
        
        visited = set()
        runs = []
        run_id = 1
        
        for station in stations:
            if station.back_point not in visited:
                component_stations = []
                queue = [station.back_point]
                component_points = set()
                
                while queue:
                    point = queue.pop(0)
                    if point in visited:
                        continue
                    visited.add(point)
                    component_points.add(point)
                    
                    for s in stations:
                        if s.back_point == point or s.fore_point == point:
                            if s not in component_stations:
                                component_stations.append(s)
                                other_point = s.fore_point if s.back_point == point else s.back_point
                                if other_point not in visited:
                                    queue.append(other_point)
                
                ordered_stations = self._order_stations(component_stations, component_points)
                
                if ordered_stations:
                    runs.append(LevelingRun(
                        run_id=run_id,
                        stations=ordered_stations,
                        start_point=ordered_stations[0].back_point,
                        end_point=ordered_stations[-1].fore_point
                    ))
                    run_id += 1
        
        return runs
    
    def _order_stations(self, stations: List[Station], points: Set[str]) -> List[Station]:
        """Упорядочивает станции в правильном порядке хода"""
        if not stations:
            return []
        
        start_candidates = [s.back_point for s in stations if s.back_point in self.points_with_heights]
        
        if start_candidates:
            start_point = start_candidates[0]
        else:
            start_point = stations[0].back_point
        
        ordered = []
        current_point = start_point
        used_stations = set()
        
        while True:
            next_station = None
            for s in stations:
                if s.back_point == current_point and id(s) not in used_stations:
                    next_station = s
                    break
            
            if next_station is None:
                break
            
            ordered.append(next_station)
            used_stations.add(id(next_station))
            current_point = next_station.fore_point
        
        return ordered


# ============================================================
# УРАВНИВАТЕЛЬ
# ============================================================

class LevelingAdjustment:
    def __init__(self, run: LevelingRun, h_start: float, h_end: Optional[float],
                 level_class: str = "technical", misclosure_mode: str = "distance"):
        self.run = run
        self.h_start = h_start
        self.h_end = h_end if h_end is not None else h_start
        self.level_class = level_class
        self.misclosure_mode = misclosure_mode

    def calculate(self) -> dict:
        if not self.run.stations:
            return {"error": "Нет станций в ходе"}

        sum_dh = sum(s.dh_mean for s in self.run.stations)
        sum_dist = sum(s.distance for s in self.run.stations)
        n_stations = len(self.run.stations)

        if sum_dist == 0:
            return {"error": "Сумма расстояний равна нулю"}

        dh_theoretical = self.h_end - self.h_start
        misclosure_m = sum_dh - dh_theoretical
        misclosure_mm = misclosure_m * 1000

        if self.misclosure_mode == "stations":
            allowed_mm = self._get_allowed_by_stations(n_stations)
            length_km = sum_dist / 1000.0
        else:
            length_km = sum_dist / 1000.0
            allowed_mm = self._get_allowed_by_distance(length_km)

        is_valid = abs(misclosure_mm) <= allowed_mm

        result = {
            "run_id": self.run.run_id,
            "start_point": self.run.start_point,
            "end_point": self.run.end_point,
            "sum_dh_m": round(sum_dh, 4),
            "dh_theoretical_m": round(dh_theoretical, 4),
            "misclosure_mm": round(misclosure_mm, 2),
            "allowed_mm": round(allowed_mm, 2),
            "length_km": round(length_km, 3),
            "n_stations": n_stations,
            "is_valid": is_valid,
            "points": [],
            "level_class": self.level_class,
            "misclosure_mode": self.misclosure_mode,
            "formula_used": self._get_formula_text(length_km, n_stations)
        }

        if is_valid:
            result["points"] = self._distribute_corrections(misclosure_m, sum_dist)
        else:
            result["error"] = f"Невязка {misclosure_mm:.2f} мм превышает допуск {allowed_mm:.2f} мм!"

        return result

    def _get_allowed_by_distance(self, length_km: float) -> float:
        formulas = {
            "I": 2.0 * math.sqrt(length_km),
            "II": 5.0 * math.sqrt(length_km),
            "III": 10.0 * math.sqrt(length_km),
            "IV": 20.0 * math.sqrt(length_km),
            "technical": 50.0 * math.sqrt(length_km),
        }
        return formulas.get(self.level_class, 50.0 * math.sqrt(length_km))

    def _get_allowed_by_stations(self, n_stations: int) -> float:
        formulas = {
            "I": 0.5 * math.sqrt(n_stations),
            "II": 0.5 * math.sqrt(n_stations),
            "III": 1.0 * math.sqrt(n_stations),
            "IV": 2.0 * math.sqrt(n_stations),
            "technical": 5.0 * math.sqrt(n_stations),
        }
        return formulas.get(self.level_class, 5.0 * math.sqrt(n_stations))

    def _get_formula_text(self, length_km: float, n_stations: int) -> str:
        if self.misclosure_mode == "stations":
            coeffs = {"I": "0.5", "II": "0.5", "III": "1.0", "IV": "2.0", "technical": "5.0"}
            coeff = coeffs.get(self.level_class, "5.0")
            return f"f_доп = ±{coeff} × √{n_stations} = ±{self._get_allowed_by_stations(n_stations):.2f} мм"
        else:
            coeffs = {"I": "2.0", "II": "5.0", "III": "10.0", "IV": "20.0", "technical": "50.0"}
            coeff = coeffs.get(self.level_class, "50.0")
            return f"f_доп = ±{coeff} × √{length_km:.3f} = ±{self._get_allowed_by_distance(length_km):.2f} мм"

    def _distribute_corrections(self, misclosure_m: float, sum_dist: float) -> List[AdjustedPoint]:
        points = []
        current_h = self.h_start
        cumulative_dist = 0.0

        points.append(AdjustedPoint(
            point_id=self.run.stations[0].back_point,
            h_original=self.h_start,
            h_adjusted=self.h_start,
            correction=0.0,
            distance_from_start=0.0
        ))

        for station in self.run.stations:
            correction_m = -misclosure_m * (station.distance / sum_dist)
            dh_adjusted = station.dh_mean + correction_m
            current_h += dh_adjusted
            cumulative_dist += station.distance

            points.append(AdjustedPoint(
                point_id=station.fore_point,
                h_original=None,
                h_adjusted=round(current_h, 4),
                correction=round(correction_m, 4),
                distance_from_start=cumulative_dist
            ))

        if len(points) > 1:
            points[-1].h_adjusted = self.h_end
            last_dh_adj = self.h_end - points[-2].h_adjusted
            points[-1].correction = last_dh_adj - self.run.stations[-1].dh_mean

        return points


# ============================================================
# ИНТЕРФЕЙС
# ============================================================

st.set_page_config(page_title="Geogeolevel", page_icon="📐", layout="wide")

st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1f77b4;
        margin-bottom: 0.5rem;
    }
    .subtitle {
        font-size: 1.1rem;
        color: #666;
        margin-bottom: 2rem;
    }
    .info-box {
        background: #d1ecf1;
        border-left: 4px solid #17a2b8;
        padding: 1rem;
        border-radius: 5px;
        margin: 1rem 0;
    }
    .success-box {
        background: #d4edda;
        border-left: 4px solid #28a745;
        padding: 1rem;
        border-radius: 5px;
        margin: 1rem 0;
    }
    .error-box {
        background: #f8d7da;
        border-left: 4px solid #dc3545;
        padding: 1rem;
        border-radius: 5px;
        margin: 1rem 0;
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">📐 Geogeolevel</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Обработка нивелирных ходов · Trimble DiNi BFFB · Уравнивание · Отчёты</div>', unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Параметры")
    
    uploaded = st.file_uploader("📂 Загрузите файл .DAT", type=["dat", "txt"])
    
    st.divider()
    
    level_class = st.selectbox(
        "Класс нивелирования",
        options=["technical", "IV", "III", "II", "I"],
        format_func=lambda x: {
            "technical": "Техническое",
            "IV": "IV класс",
            "III": "III класс",
            "II": "II класс",
            "I": "I класс",
        }[x],
        index=0,
        key="class_select"
    )
    
    misclosure_mode = st.radio(
        "Режим расчета допуска",
        options=["distance", "stations"],
        format_func=lambda x: "По длине хода (км)" if x == "distance" else "По числу станций",
        index=0,
        key="misclosure_mode"
    )
    
    st.divider()
    
    h_start_override = st.number_input("H начальной (м, необязательно)", value=None, format="%.3f", key="h_start")
    h_end_override = st.number_input("H конечной (м, необязательно)", value=None, format="%.3f", key="h_end")

if not uploaded:
    st.info(" Загрузите файл .DAT в панели слева для начала работы")
    st.stop()

try:
    parser = TrimbleDiniBFFBParser(uploaded.read())
    job_name, measurements, points_with_heights = parser.parse()
    
    st.success(f"✅ Файл прочитан: **{job_name}**")
    
    analyzer = RunAnalyzer(measurements, points_with_heights)
    stations = analyzer.group_into_stations()
    runs = analyzer.identify_runs(stations)
    
    st.info(f" Найдено: **{len(measurements)}** измерений (включая контрольные), **{len(stations)}** станций, **{len(runs)}** ход(ов)")
    
    if points_with_heights:
        with st.expander("📍 Найденные исходные отметки"):
            for pt, h in sorted(points_with_heights.items()):
                st.text(f"{pt}: {h:.5f} м")
    
    all_results = []
    all_dfs = []
    
    for run in runs:
        h_start = h_start_override if h_start_override is not None else points_with_heights.get(run.start_point, 100.000)
        h_end = h_end_override if h_end_override is not None else points_with_heights.get(run.end_point, None)
        
        engine = LevelingAdjustment(run, h_start, h_end, level_class, misclosure_mode)
        result = engine.calculate()
        all_results.append(result)
        
        if result.get("points"):
            df = pd.DataFrame([{
                "Ход": f"Ход {result['run_id']}",
                "Точка": p.point_id,
                "H исх., м": f"{p.h_original:.5f}" if p.h_original else "—",
                "Поправка, мм": round(p.correction * 1000, 2),
                "H уравн., м": round(p.h_adjusted, 4),
                "Расст., м": round(p.distance_from_start, 1),
            } for p in result["points"]])
            all_dfs.append(df)
    
    st.divider()
    st.subheader("📊 Результаты уравнивания")
    
    for idx, result in enumerate(all_results):
        st.markdown(f"### Ход {result['run_id']}: {result['start_point']} → {result['end_point']}")
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Длина хода", f"{result['length_km']:.3f} км")
        col2.metric("Число станций", result['n_stations'])
        col3.metric("Невязка fh", f"{result['misclosure_mm']:.2f} мм")
        col4.metric("Допуск", f"±{result['allowed_mm']:.2f} мм")
        
        st.markdown(f'<div class="info-box"> {result["formula_used"]}</div>', unsafe_allow_html=True)
        
        if result["is_valid"]:
            st.markdown('<div class="success-box">✅ Невязка в допуске — ход уравнен</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="error-box">🚨 {result["error"]}</div>', unsafe_allow_html=True)
        
        if idx < len(all_dfs):
            st.dataframe(all_dfs[idx], use_container_width=True, hide_index=True)
        
        if result.get("points"):
            fig = go.Figure()
            
            distances = [p.distance_from_start for p in result["points"]]
            heights = [p.h_adjusted for p in result["points"]]
            point_ids = [p.point_id for p in result["points"]]
            
            fig.add_trace(go.Scatter(
                x=distances, y=heights,
                mode="lines+markers+text",
                text=point_ids,
                textposition="top center",
                name=f"Ход {result['run_id']}",
                line=dict(color="#1f77b4", width=2),
                marker=dict(size=10)
            ))
            
            fig.update_layout(
                title=f"Профиль хода {result['run_id']}",
                xaxis_title="Расстояние от начала, м",
                yaxis_title="Отметка, м",
                height=400,
                template="plotly_white",
                showlegend=False
            )
            st.plotly_chart(fig, use_container_width=True)
    
    if all_dfs:
        st.divider()
        st.subheader("💾 Экспорт результатов")
        
        combined_df = pd.concat(all_dfs, ignore_index=True)
        
        col_a, col_b = st.columns(2)
        
        csv = combined_df.to_csv(index=False).encode('utf-8-sig')
        col_a.download_button(
            "📥 Скачать CSV",
            data=csv,
            file_name=f"{job_name}_report.csv",
            mime="text/csv",
            use_container_width=True
        )
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            combined_df.to_excel(writer, sheet_name='Отчёт', index=False)
        col_b.download_button(
            "📥 Скачать Excel",
            data=buffer.getvalue(),
            file_name=f"{job_name}_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

except Exception as e:
    st.error(f"❌ Ошибка обработки: {e}")
    import traceback
    st.code(traceback.format_exc())

st.divider()
st.caption("Geogeolevel v4.2 · Обработка нивелирных ходов · Схема BFFB")
