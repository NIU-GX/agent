<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="brand">Agent Platform</div>
      <el-menu router :default-active="$route.path">
        <el-menu-item index="/">对话</el-menu-item>
        <el-menu-item index="/docs">知识库</el-menu-item>
        <el-menu-item index="/eval">Eval</el-menu-item>
        <el-menu-item index="/usage">用量</el-menu-item>
      </el-menu>
    </el-aside>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
</script>

<style>
html, body, #app { height: 100%; margin: 0; }
.layout { height: 100%; }
.aside {
  border-right: 1px solid #e5e7eb;
  background: #f8fafc;
}
.brand {
  font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
  font-weight: 700;
  font-size: 18px;
  padding: 20px 16px 12px;
  letter-spacing: 0.02em;
}
</style>
