
(defun c:DoPrint ()
  (command "_.-PLOT" 
    "_Yes"           ; Detailed plot configuration
    ""               ; Layout name (пусто = текущий)
    "DWG To PDF.pc3" ; Имя принтера
    "ISO full bleed A3 (297.00 x 420.00 MM)" ; Формат бумаги
    "_Millimeters"   ; Единицы
    "_Landscape"     ; Ориентация
    "_No"            ; Перевернуть?
    "_Extents"       ; Что печатать: Границы
    "_Fit"           ; Масштаб: Вписать
    "_Center"        ; Смещение: Центр
    "_Yes"           ; Печать со стилями?
    "monochrome.ctb"          ; Имя файла CTB (например monochrome.ctb)
    "_Yes"           ; Учитывать веса линий?
    "_No"            ; Масштабировать веса линий?
    "_No"            ; Печатать объекты листа последними?
    "_No"            ; Скрыть объекты?
    "C:/Users/makeden/Documents/югдорпроект_конвертер_PDF/cad_server_workdir/Несколько_рамок_в_принудительном_порядке_в_листе.pdf" ; Путь к выходному файлу
    "_No"            ; Сохранить изменения?
    "_Yes"           ; Приступить к печати
  )
  (command "_.QUIT" "_Y")
)
