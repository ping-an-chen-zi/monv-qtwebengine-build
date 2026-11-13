# -*- coding: utf-8 -*-
"""Apply the NativeSkiaOutputDevice buffer-pool patch to QtWebEngine 6.11.2.

Usage: python apply_patch.py <qtwebengine-source-root>
"""
import pathlib
import sys

root = pathlib.Path(sys.argv[1]) / "src" / "core" / "compositor"
h = root / "native_skia_output_device.h"
c = root / "native_skia_output_device.cpp"

hs = h.read_text(encoding="utf-8")
cs = c.read_text(encoding="utf-8")

hs2 = hs.replace("#include <QMutex>", "#include <QMutex>\n#include <vector>", 1)
assert hs2 != hs, "header include marker not found"

old_priv = "    void SwapBuffersFinished();\n"
new_priv = (
    "    void SwapBuffersFinished();\n\n"
    "    // Reuse buffers per Shape: avoid allocating a new SharedImage on every resize.\n"
    "    std::unique_ptr<Buffer> takeReusableBuffer(const Shape &shape);\n"
    "    void recycleBuffer(std::unique_ptr<Buffer> buffer);\n"
    "    std::vector<std::unique_ptr<Buffer>> m_bufferPool;\n"
)
assert old_priv in hs2, "header private marker not found"
hs2 = hs2.replace(old_priv, new_priv, 1)

helpers = r'''
std::unique_ptr<NativeSkiaOutputDevice::Buffer>
NativeSkiaOutputDevice::takeReusableBuffer(const Shape &shape)
{
    for (auto it = m_bufferPool.begin(); it != m_bufferPool.end(); ++it) {
        if ((*it)->shape() == shape) {
            auto buffer = std::move(*it);
            m_bufferPool.erase(it);
            return buffer;
        }
    }
    return nullptr;
}

void NativeSkiaOutputDevice::recycleBuffer(std::unique_ptr<Buffer> buffer)
{
    if (!buffer)
        return;
    buffer->endPresent();
    buffer->freeTexture();
    constexpr size_t kMaxPooledBuffers = 6;
    if (m_bufferPool.size() >= kMaxPooledBuffers)
        m_bufferPool.erase(m_bufferPool.begin());
    m_bufferPool.push_back(std::move(buffer));
}

'''
marker = "bool NativeSkiaOutputDevice::Reshape(const ReshapeParams &params)"
assert marker in cs, "Reshape marker not found"
cs2 = cs.replace(marker, helpers + marker, 1)

old_begin = """    {
        QMutexLocker locker(&m_mutex);
        if (!m_backBuffer || m_backBuffer->shape() != m_shape) {
            m_backBuffer = std::make_unique<Buffer>(this);
            if (!m_backBuffer->initialize())
                return nullptr;
        }
    }
"""
new_begin = """    {
        QMutexLocker locker(&m_mutex);
        if (!m_backBuffer || m_backBuffer->shape() != m_shape) {
            if (m_backBuffer)
                recycleBuffer(std::move(m_backBuffer));
            m_backBuffer = takeReusableBuffer(m_shape);
            if (!m_backBuffer) {
                m_backBuffer = std::make_unique<Buffer>(this);
                if (!m_backBuffer->initialize())
                    return nullptr;
            }
        }
    }
"""
assert old_begin in cs2, "BeginPaint block not found"
cs2 = cs2.replace(old_begin, new_begin, 1)

old_swap = """        if (m_middleBuffer)
            m_middleBuffer->freeTexture();
"""
new_swap = """        if (m_middleBuffer)
            recycleBuffer(std::move(m_middleBuffer));
"""
assert old_swap in cs2, "swapFrame block not found"
cs2 = cs2.replace(old_swap, new_swap, 1)

old_dtor = """NativeSkiaOutputDevice::Buffer::~Buffer()
{
    // FIXME: Can't be called in case of threaded rendering with unexposed window.
    //DCHECK(!textureCleanupCallback);
    if (textureCleanupCallback)
        qWarning("NativeSkiaOutputDevice: Leaking graphics resources.");
"""
new_dtor = """NativeSkiaOutputDevice::Buffer::~Buffer()
{
    if (textureCleanupCallback)
        freeTexture();
"""
assert old_dtor in cs2, "Buffer destructor block not found"
cs2 = cs2.replace(old_dtor, new_dtor, 1)

h.write_text(hs2, encoding="utf-8")
c.write_text(cs2, encoding="utf-8")
print("patch applied to", root)
