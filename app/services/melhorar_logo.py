#!/usr/bin/env python3
from PIL import Image, ImageEnhance, ImageFilter
import sys
from pathlib import Path

def melhorar_logo(input_path, output_path, size=(90, 29)):
    """Criar logo nítido com máxima qualidade"""
    
    try:
        # Abrir imagem
        img = Image.open(input_path)
        print(f"Logo original: {img.size}")
        
        # Se for maior, redimensionar com alta qualidade
        if img.size != size:
            # Usar LANCZOS para melhor qualidade
            img = img.resize(size, Image.Resampling.LANCZOS)
            print(f"Redimensionado para: {size}")
        
        # Aplicar nitidez
        enhancer = ImageEnhance.Sharpness(img)
        img = enhancer.enhance(1.5)  # 1.5x mais nítido
        
        # Aplicar filtro de nitidez adicional
        img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=150, threshold=3))
        
        # Salvar com máxima qualidade
        img.save(output_path, "PNG", compress_level=0, optimize=False)
        
        print(f"✓ Logo salvo em: {output_path}")
        print(f"  Tamanho final: {img.size}")
        print(f"  Arquivo: {Path(output_path).stat().st_size} bytes")
        
        return True
        
    except Exception as e:
        print(f"✗ Erro: {e}")
        return False

if __name__ == "__main__":
    # Estamos DENTRO de app/services/ - usar diretório atual
    logo_dir = Path(".")
    
    # Procurar logo original - CORRIGIDO para diretório atual
    if (logo_dir / "logo-Econdominio-celular01.png").exists():
        input_file = "logo-Econdominio-celular01.png"
        print(f"Usando logo original: {input_file}")
    elif (logo_dir / "logo.png").exists():
        input_file = "logo.png"
        print(f"Usando logo existente: {input_file}")
    else:
        print("Nenhum logo encontrado!")
        print(f"Arquivos na pasta: {list(logo_dir.glob('*.png'))}")
        sys.exit(1)
    
    # Fazer backup
    backup_file = "logo.png.backup"
    if not Path(backup_file).exists():
        import shutil
        shutil.copy("logo.png", backup_file)
        print(f"Backup criado: {backup_file}")
    
    # Melhorar logo
    output_file = "logo_melhorado.png"
    if melhorar_logo(input_file, output_file):
        print("\n✅ Logo melhorado criado com sucesso!")
        print(f"Agora execute: mv logo_melhorado.png logo.png")
