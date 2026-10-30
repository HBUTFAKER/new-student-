import Papa from 'papaparse'

export interface CsvColumn<T> {
  key: keyof T & string
  label: string
  format?: (row: T) => string
}

function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

function cell(value: unknown): string {
  const text = value === null || value === undefined ? '' : String(value)
  return `"${text.replace(/"/g, '""')}"`
}

/** UTF-8 带 BOM，Excel 直接双击不乱码 */
export function exportCsv<T>(filename: string, columns: CsvColumn<T>[], rows: T[]): void {
  const header = columns.map((column) => cell(column.label)).join(',')
  const body = rows.map((row) =>
    columns.map((column) => cell(column.format ? column.format(row) : row[column.key])).join(','),
  )
  const content = `\uFEFF${[header, ...body].join('\r\n')}`
  download(new Blob([content], { type: 'text/csv;charset=utf-8' }), filename)
}

/** 导入模板：表头 + 一行示例 */
export function downloadCsvTemplate(
  filename: string,
  headers: string[],
  sample: string[],
): void {
  const content = `\uFEFF${[headers.map(cell).join(','), sample.map(cell).join(',')].join('\r\n')}`
  download(new Blob([content], { type: 'text/csv;charset=utf-8' }), filename)
}

export interface ParsedCsv {
  headers: string[]
  rows: Record<string, string>[]
}

export function parseCsvFile(file: File): Promise<ParsedCsv> {
  return new Promise((resolve, reject) => {
    Papa.parse<Record<string, string>>(file, {
      header: true,
      skipEmptyLines: 'greedy',
      transformHeader: (header) => header.replace(/^\uFEFF/, '').trim(),
      complete: (result) => {
        if (result.errors.length > 0 && result.data.length === 0) {
          reject(new Error(result.errors[0]?.message ?? 'CSV 解析失败'))
          return
        }
        const headers = (result.meta.fields ?? []).map((field) => field.trim())
        const rows = result.data
          .map((row) => {
            const cleaned: Record<string, string> = {}
            for (const [key, value] of Object.entries(row)) {
              cleaned[key.trim()] = (value ?? '').trim()
            }
            return cleaned
          })
          .filter((row) => Object.values(row).some((value) => value !== ''))
        resolve({ headers, rows })
      },
      error: (error: Error) => reject(error),
    })
  })
}
