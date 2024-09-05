# CREATED: 11/9/15 3:57 PM by Justin Salamon <justin.salamon@nyu.edu>

import soundfile
import resampy
import vamp
import argparse
import os
import numpy as np
from midiutil.MidiFile import MIDIFile
from scipy.signal import medfilt
import jams
import __init__

'''
Extract the melody from an audio file and convert it to MIDI.

The script extracts the melody from an audio file using the Melodia algorithm,
and then segments the continuous pitch sequence into a series of quantized
notes, and exports to MIDI using the provided BPM. If the --jams option is
specified the script will also save the output as a JAMS file. Note that the
JAMS file uses the original note onset/offset times estimated by the algorithm
and ignores the provided BPM value.

Note: Melodia can work pretty well and is the result of several years of
research. The note segmentation/quantization code was hacked in about 30
minutes. Proceed at your own risk... :)

usage: audio_to_midi_melodia.py [-h] [--smooth SMOOTH]
                                [--minduration MINDURATION] [--jams]
                                infile outfile bpm


Examples:
python audio_to_midi_melodia.py --smooth 0.25 --minduration 0.1 --jams
                                ~/song.wav ~/song.mid 60
'''

# Функция для сохранения результата в формате JAMS
def save_jams(jamsfile, notes, track_duration, orig_filename):
    jam = jams.JAMS()  # Создаем новый объект JAMS

    # Сохраняем метаданные трека (длительность и имя исходного файла)
    jam.file_metadata.duration = track_duration
    jam.file_metadata.title = orig_filename

    # Создаем аннотацию для MIDI нот
    midi_an = jams.Annotation(namespace='pitch_midi', duration=track_duration)
    midi_an.annotation_metadata = jams.AnnotationMetadata(
        data_source='audio_to_midi_melodia.py v%s' % __init__.__version__,
        annotation_tools='audio_to_midi_melodia.py (https://github.com/justinsalamon/audio_to_midi_melodia)'
    )

    # Добавляем ноты в аннотацию
    for n in notes:
        midi_an.append(time=n[0], duration=n[1], value=n[2], confidence=0)

    # Сохраняем аннотацию в JAMS файл
    jam.annotations.append(midi_an)
    jam.save(jamsfile)

# Функция для сохранения нот в MIDI файл
def save_midi(outfile, notes, tempo):
    track = 0  # номер трека
    time = 0  # начальное время
    midifile = MIDIFile(1)  # создаем новый MIDI файл с одним треком

    # Добавляем название трека и темп
    midifile.addTrackName(track, time, "MIDI TRACK")
    midifile.addTempo(track, time, tempo)

    channel = 0  # канал MIDI
    volume = 100  # громкость

    # Добавляем ноты в трек
    for note in notes:
        onset = note[0] * (tempo / 60.)  # пересчет времени начала ноты
        duration = note[1] * (tempo / 60.)  # пересчет длительности
        pitch = note[2]  # высота тона
        midifile.addNote(track, channel, pitch, onset, duration, volume)

    # Сохраняем MIDI файл на диск
    with open(outfile, 'wb') as binfile:
        midifile.writeFile(binfile)

# Функция для сегментации последовательности в MIDI ноты
def midi_to_notes(midi, fs, hop, smooth, minduration):
    # Применяем медианную фильтрацию к последовательности MIDI нот (сглаживание)
    if smooth > 0:
        filter_duration = smooth  # длительность фильтра
        filter_size = int(filter_duration * fs / float(hop))
        if filter_size % 2 == 0:  # размер фильтра должен быть нечетным
            filter_size += 1
        midi_filt = medfilt(midi, filter_size)  # применяем медианный фильтр
    else:
        midi_filt = midi

    notes = []  # список для хранения нот
    p_prev = None  # предыдущая нота
    duration = 0  # длительность текущей ноты
    onset = 0  # время начала текущей ноты

    # Проходим по последовательности MIDI нот
    for n, p in enumerate(midi_filt):
        if p == p_prev:
            duration += 1  # продолжаем ноту
        else:
            if p_prev > 0:  # если предыдущая нота была не паузой
                # Рассчитываем длительность ноты в секундах
                duration_sec = duration * hop / float(fs)
                if duration_sec >= minduration:  # добавляем только ноты, которые длиннее минимальной длительности
                    onset_sec = onset * hop / float(fs)
                    notes.append((onset_sec, duration_sec, p_prev))

            # Начинаем новую ноту
            onset = n
            duration = 1
            p_prev = p

    # Добавляем последнюю ноту
    if p_prev > 0:
        duration_sec = duration * hop / float(fs)
        onset_sec = onset * hop / float(fs)
        notes.append((onset_sec, duration_sec, p_prev))

    return notes

# Преобразование частоты в MIDI ноты
def hz2midi(hz):
    hz_nonneg = hz.copy()  # копируем массив частот
    idx = hz_nonneg <= 0  # заменяем отрицательные и нулевые частоты
    hz_nonneg[idx] = 1
    midi = 69 + 12 * np.log2(hz_nonneg / 440.)  # преобразуем частоты в MIDI ноты
    midi[idx] = 0  # заменяем нулевые значения
    return np.round(midi)  # округляем результат

# Основная функция для преобразования аудио в MIDI
def audio_to_midi_melodia(infile, outfile, bpm, smooth=0.25, minduration=0.1, savejams=False):
    fs = 44100  # частота дискретизации
    hop = 128  # шаг анализа

    # Загрузка аудиофайла
    print("Loading audio...")
    data, sr = soundfile.read(infile)
    if len(data.shape) > 1 and data.shape[1] > 1:  # если файл стерео
        data = data.mean(axis=1)  # преобразуем в моно
    if sr != fs:
        data = resampy.resample(data, sr, fs)  # ресемплируем до 44100 Гц
        sr = fs

    # Извлечение мелодии с помощью алгоритма Melodia
    print("Extracting melody f0 with MELODIA...")
    melody = vamp.collect(data, sr, "mtg-melodia:melodia", parameters={"voicing": 0.2})
    pitch = melody['vector'][1]

    # Заполнение отсутствующих значений в начале последовательности
    pitch = np.insert(pitch, 0, [0] * 8)

    # Преобразование частоты в MIDI ноты
    print("Converting Hz to MIDI notes...")
    midi_pitch = hz2midi(pitch)

    # Сегментация последовательности в ноты
    notes = midi_to_notes(midi_pitch, fs, hop, smooth, minduration)

    # Сохранение MIDI файла
    print("Saving MIDI to disk...")
    save_midi(outfile, notes, bpm)

    # Сохранение JAMS файла, если указана опция
    if savejams:
        print("Saving JAMS to disk...")
        jamsfile = os.path.splitext(outfile)[0] + ".jams"
        track_duration = len(data) / float(fs)
        save_jams(jamsfile, notes, track_duration, os.path.basename(infile))

    print("Conversion complete.")


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("infile", help="Path to input audio file.")
    parser.add_argument("outfile", help="Path for saving output MIDI file.")
    parser.add_argument("bpm", type=int, help="Tempo of the track in BPM.")
    parser.add_argument("--smooth", type=float, default=0.25,
                        help="Smooth the pitch sequence with a median filter "
                             "of the provided duration (in seconds).")
    parser.add_argument("--minduration", type=float, default=0.1,
                        help="Minimum allowed duration for note (in seconds). "
                             "Shorter notes will be removed.")
    parser.add_argument("--jams", action="store_const", const=True,
                        default=False, help="Also save output in JAMS format.")

    args = parser.parse_args()

    audio_to_midi_melodia(args.infile, args.outfile, args.bpm,
                          smooth=args.smooth, minduration=args.minduration,
                          savejams=args.jams)
