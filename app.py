"""
GeoLevel Cloud v3.2 - Исправлен допуск для II класса (5*sqrt(L))
"""

import io
import math
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


@dataclass
class LevelingStation:
    point_from: str
    point_to: str
    dh_measured: float
    distance: float


@dataclass
class AdjustedPoint:
    point_id: str
    h_original: Optional[float]
    h_adjusted: float
    correction: float


class LevelingAdjustment:
    def __init__(self, h_start, h_end, stations, level_class="technical"):
        self.h_start = h_start
        self.h_end = h_end if h_end is not None else h_start
        self.stations = stations
        self.level_class = level_class

    def calculate(self):
        if not self.stations:
            return {"error": "Нет данных для уравнивания"}

        sum_dh = sum(s.dh_measured for s in self.stations)
        sum_dist = sum(s.distance for s in self.stations)

        if sum_dist == 0:
            return {"error": "Сумма расстояний равна нулю"}

        dh_theoretical = self.h_end - self.h_start
        misclosure_m = sum_dh - dh_theoretical
        misclosure_mm = misclosure_m * 1000

        length_km = sum_dist / 1000.0
        allowed_mm = self._get_allowed_misclosure(length_km)
        is_valid = abs(misclosure_mm) <= allowed_mm

        result = {
            "sum_dh_m": round(sum_dh, 4),
            "dh_theoretical_m": round(dh_theoretical, 4),
            "misclosure_mm": round(misclosure_mm, 2),
            "allowed_mm": round(allowed_mm, 2),
            "length_km": round(length_km, 3),
            "is_valid": is_valid,
            "points": [],
            "level_class": self.level_class,
            "formula_used": self._get_formula_text(length_km)
        }

        if is_valid:
            result["points"] = self._distribute_corrections(misclosure_m, sum_dist)
        else:
            result["error"] = f"Невязка {misclosure_mm:.2f} мм превышает допуск {allowed_mm:.2f} мм!"
        return result

    def _get_allowed_misclosure(self, length_km):
        # ИСПРАВЛЕНО: II класс теперь 5.0 * sqrt(L) согласно СП 47.13330.2016
        formulas = {
            "I": 2.0 * math.sqrt(length_km),
            "II": 5.0 * math.sqrt(length_km),
            "III": 10.0 * math.sqrt(length_km),
            "IV": 20.0 * math.sqrt(length_km),
            "technical": 50.0 * math.sqrt(length_km),
        }
        return formulas.get(self.level_class, 50.0 * math.sqrt(length_km))

    def _get_formula_text(self, length_km):
        coeffs = {
            "I": "2.0", "II": "5.0", "III": "10.0", 
            "IV": "20.0", "technical": "50.0"
        }
        coeff = coeffs.get(self.level_class, "50.0")
        return f"f_доп = ±{coeff} × √{length_km:.3f} = ±{self._get_allowed_misclosure(length_km):.2f} мм"

    def _distribute_corrections(self, misclosure_m, sum_dist):
        points = []
        current_h = self.h_start
        points.append(AdjustedPoint(
            point_id=self.stations[0].point_from,
            h_original=self.h_start,
            h_adjusted=self.h_start,
            correction=0.0
        ))

        for station in self.stations:
            correction_m = -misclosure_m * (station.distance / sum_dist)
            dh_adjusted = station.dh_measured + correction_m
            current_h += dh_adjusted
            points.append(AdjustedPoint(
                point_id=station.point_to,
                h_original=None,
                h_adjusted=round(current_h, 4),
                correction=round(correction_m, 4)
            ))

        if len(points) > 1:
            points[-1].h_adjusted = self.h_end
            last_dh_adj = self.h_end - points[-2].h_adjusted
            points[-1].correction = last_dh_adj - self.stations[-1].dh_measured
        return points


class TrimbleDiniBFFBParser:
    def __init__(self, raw_bytes):
        self.raw_bytes = raw_bytes

    def parse(self):
        text = self._decode_text()
        lines = text.splitlines()
        
        stations = []
        job_name = "Unknown Job"
        points_with_heights = {}
        
        for line in lines[:5]:
            if 'TO' in line and '.dat' in line.lower():
                match = re.search(r'TO\s+(\S+\.dat)', line, re.IGNORECASE)
                if match:
                    job_name = match.group(1).replace('.dat', '')
                break
        
        current_back_point = None
        current_back_reading = None
        current_back_dist = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            z_match = re.search(r'\|Z\s+([+-]?\d+\.?\d*)\s*m\s*\|', line)
            if z_match:
                point_match = re.search(r'\|KD1\s+(\S+)\s+', line)
                if point_match:
                    point_name = point_match.group(1)
                    height = float(z_match.group(1))
                    points_with_heights[point_name] = height
                continue
            
            rb_match = re.search(r'\|Rb\s+([+-]?\d+\.?\d*)\s*m\s*\|HD\s+([+-]?\d+\.?\d*)\s*m\s*\|', line)
            if rb_match:
                point_match = re.search(r'\|KD1\s+(\S+)\s+', line)
                if point_match:
                    current_back_point = point_match.group(1)
                    current_back_reading = float(rb_match.group(1))
                    current_back_dist = float(rb_match.group(2))
                continue
            
            rf_match = re.search(r'\|Rf\s+([+-]?\d+\.?\d*)\s*m\s*\|HD\s+([+-]?\d+\.?\d*)\s*m\s*\|', line)
            if rf_match and current_back_point and current_back_reading is not None:
                point_match = re.search(r'\|KD1\s+(\S+)\s+', line)
                if point_match:
                    fore_point = point_match.group(1)
                    fore_reading = float(rf_match.group(1))
                    fore_dist = float(rf_match.group(2))
                    
                    dh = current_back_reading - fore_reading
                    dist = current_back_dist + fore_dist
                    
                    stations.append(LevelingStation(
                        point_from=current_back_point,
                        point_to=fore_point,
                        dh_measured=round(dh, 5),
                        distance=round(dist, 3)
                    ))
                    
                    current_back_point = None
                    current_back_reading = None
                    current_back_dist = None
        
        return job_name, stations, points_with_heights

    def _decode_text(self):
        for enc in ['utf-8', 'windows-1251', 'cp1251', 'latin-1']:
            try:
                return self.raw_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.raw_bytes.decode('latin-1', errors='ignore')


def get_demo_stations():
    return [
        LevelingStation("RP12", "X1", -1.45702, 73.504),
        LevelingStation("X1", "X2", -0.70624, 77.837),
        LevelingStation("X2", "RP22", 0.66686, 67.408),
    ]


# ============ ИНТЕРФЕЙС ============
st.set_page_config(page_title="GeoLevel Cloud", page_icon="📐", layout="wide")
st.title("📐 GeoLevel Cloud v3.2")
st.caption("Trimble DiNi BFFB · Уравнивание · Отчёты (Нормы СП 47.13330.2016)")

with st.sidebar:
    st.header("⚙️ Параметры хода")
    
    data_source = st.radio(
        "Источник данных",
        ["📂 Файл DiNi (.DAT)", "📝 Ввести вручную", "🎮 Демо-данные"],
        index=2,
        key="source_radio"
    )
    
    st.divider()
    
    level_class = st.selectbox(
        "Класс нивелирования",
        options=["technical", "IV", "III", "II", "I"],
        format_func=lambda x: {
            "technical": "Техническое (±50√L)",
            "IV": "IV класс (±20√L)",
            "III": "III класс (±10√L)",
            "II": "II класс (±5√L)",
            "I": "I класс (±2√L)",
        }[x],
        index=0,
        key="class_select"
    )
    
    st.divider()
    
    h_start = st.number_input("H начальной точки (м)", value=100.000, format="%.3f", key="h_start")
    h_end = st.number_input("H конечной точки (м)", value=99.500, format="%.3f", key="h_end")

stations = []
job_name = "Ручной ввод"
points_heights = {}

if data_source.startswith("📂"):
    uploaded = st.file_uploader("Загрузите файл .DAT", type=["dat", "txt"], key="file_uploader")
    if uploaded:
        try:
            parser = TrimbleDiniBFFBParser(uploaded.read())
            job_name, stations, points_heights = parser.parse()
            
            st.success(f"✅ Файл прочитан. Работа: **{job_name}**")
            st.info(f"📊 Станций: {len(stations)} | Точек с отметками: {len(points_heights)}")
            
            if points_heights:
                with st.expander("📍 Найденные исходные отметки"):
                    for pt, h in points_heights.items():
                        st.text(f"{pt}: {h:.5f} м")
            
            if len(stations) == 0:
                st.warning("⚠️ Не найдено превышений. Проверь формат файла.")
                
        except Exception as e:
            st.error(f"❌ Ошибка парсинга: {e}")

elif data_source.startswith("📝"):
    st.info("Формат: `Откуда, Куда, Превышение (м), Расстояние (м)`")
    raw_text = st.text_area(
        "Данные",
        value="RP12, X1, -1.457, 73.5\nX1, X2, -0.706, 77.8\nX2, RP22, 0.667, 67.4",
        height=200,
        key="manual_data"
    )
    if st.button("Распарсить данные", key="parse_btn"):
        prev = None
        for line in raw_text.strip().splitlines():
            parts = [p.strip() for p in line.split(',')]
            if len(parts) == 4:
                try:
                    if prev is None:
                        prev = parts[0]
                    stations.append(LevelingStation(prev, parts[0], float(parts[2]), float(parts[3])))
                    prev = parts[0]
                except ValueError:
                    pass
        if stations:
            st.success(f"✅ Распознано {len(stations)} станций")

else:
    stations = get_demo_stations()
    st.info("🎮 Используются демо-данные (3 станции)")

# ============ РАСЧЕТ И РЕЗУЛЬТАТЫ ============
if stations:
    calc_h_start = h_start
    calc_h_end = h_end
    
    if points_heights and len(stations) > 0:
        first_point = stations[0].point_from
        last_point = stations[-1].point_to
        if first_point in points_heights:
            calc_h_start = points_heights[first_point]
        if last_point in points_heights:
            calc_h_end = points_heights[last_point]
    
    st.divider()
    st.subheader(f"⚙️ Выбранный класс: **{level_class.upper()}**")
    
    engine = LevelingAdjustment(calc_h_start, calc_h_end, stations, level_class)
    result = engine.calculate()

    if "error" in result and not result.get("points"):
        st.error(f"🚨 {result['error']}")
    else:
        st.subheader(f"📊 Отчёт по работе: {job_name}")
        
        col_info1, col_info2 = st.columns(2)
        with col_info1:
            st.metric("Длина хода", f"{result['length_km']:.3f} км")
            st.metric("Σ превышений", f"{result['sum_dh_m']:.4f} м")
        with col_info2:
            st.metric("Невязка fh", f"{result['misclosure_mm']:.2f} мм")
            st.metric("Допуск f_доп", f"±{result['allowed_mm']:.2f} мм")
        
        st.info(f"📐 Формула допуска ({result['level_class'].upper()}): `{result['formula_used']}`")
        
        if result["is_valid"]:
            st.success("✅ Невязка в допуске — ход уравнен!")
        else:
            st.error(f"🚨 {result['error']}")

        df = pd.DataFrame([{
            "Точка": p.point_id,
            "H исх., м": f"{p.h_original:.5f}" if p.h_original else "—",
            "Поправка, мм": round(p.correction * 1000, 2),
            "H уравненная, м": round(p.h_adjusted, 4),
        } for p in result["points"]])

        st.dataframe(df, use_container_width=True, hide_index=True)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["Точка"], y=df["H уравненная, м"],
            mode="lines+markers+text",
            text=[f"{v:.3f}" for v in df["H уравненная, м"]],
            textposition="top center",
            line=dict(color="#1f77b4", width=2),
            marker=dict(size=10)
        ))
        fig.update_layout(
            title="Продольный профиль нивелирного хода",
            xaxis_title="Точки", yaxis_title="Отметка, м",
            height=400, template="plotly_white"
        )
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("💾 Экспорт результатов")
        col_a, col_b = st.columns(2)
        
        csv = df.to_csv(index=False).encode('utf-8-sig')
        col_a.download_button(
            "📥 Скачать CSV",
            data=csv,
            file_name=f"{job_name}_report.csv",
            mime="text/csv",
            use_container_width=True
        )
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Отчёт', index=False)
        col_b.download_button(
            "📥 Скачать Excel",
            data=buffer.getvalue(),
            file_name=f"{job_name}_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
else:
    st.info("⬅️ Загрузите данные в панели слева или выберите источник")

st.divider()
st.caption("GeoLevel Cloud v3.2 · Нормы СП 47.13330.2016")
