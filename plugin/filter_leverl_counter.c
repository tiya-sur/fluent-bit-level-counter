#include <fluent-bit/flb_filter.h>
#include <fluent-bit/flb_utils.h>
#include <fluent-bit/flb_log.h>
#include <msgpack.h>
#include <string.h>
#include "filter_level_counter.h"
#include <fluent-bit/flb_filter_plugin.h>
#include <stdio.h>

static int global_count = 0;

struct level_counters {
    int debug;
    int info;
    int warning;
    int error;
    int critical;
    int unknown;
};

static struct level_counters counters = {0};

static int get_count(const char *level) {
    if (strcmp(level, "DEBUG") == 0) return ++counters.debug;
    if (strcmp(level, "INFO") == 0) return ++counters.info;
    if (strcmp(level, "WARNING") == 0) return ++counters.warning;
    if (strcmp(level, "ERROR") == 0) return ++counters.error;
    if (strcmp(level, "CRITICAL") == 0) return ++counters.critical;
    return ++counters.unknown;
}

static int cb_filter(const void *data, size_t bytes,
                     const char *tag, int tag_len,
                     void **out_buf, size_t *out_bytes,
                     struct flb_filter_instance *f_ins,
                     struct flb_input_instance *i_ins,
                     void *context,
                     struct flb_config *config)
{
    msgpack_unpacked result;
    msgpack_unpacked_init(&result);
        msgpack_sbuffer tmp_sbuf;
    msgpack_sbuffer_init(&tmp_sbuf);
    msgpack_packer tmp_pck;
    msgpack_packer_init(&tmp_pck, &tmp_sbuf, msgpack_sbuffer_write);

    size_t off = 0;

    while (msgpack_unpack_next(&result, data, bytes, &off)) {
        msgpack_object root = result.data;

        if (root.type != MSGPACK_OBJECT_ARRAY) continue;

        msgpack_object map = root.via.array.ptr[1];

        const char *level = "UNKNOWN";

        for (int i = 0; i < map.via.map.size; i++) {
            msgpack_object key = map.via.map.ptr[i].key;
            msgpack_object val = map.via.map.ptr[i].val;

            if (key.type == MSGPACK_OBJECT_STR &&
                strncmp(key.via.str.ptr, "level", key.via.str.size) == 0) {
char level_buf[32];
snprintf(level_buf, sizeof(level_buf), "%.*s", (int)val.via.str.size, val.via.str.ptr);
level = level_buf;            }
        }

        int count = get_count(level);
        global_count++;
        msgpack_pack_array(&tmp_pck, 2);
        msgpack_pack_object(&tmp_pck, root.via.array.ptr[0]);

        msgpack_pack_map(&tmp_pck, map.via.map.size + 2);

        for (int i = 0; i < map.via.map.size; i++) {
            msgpack_pack_object(&tmp_pck, map.via.map.ptr[i].key);
            msgpack_pack_object(&tmp_pck, map.via.map.ptr[i].val);
        }

        msgpack_pack_str(&tmp_pck, 5);
        msgpack_pack_str_body(&tmp_pck, "count", 5);
                msgpack_pack_int(&tmp_pck, count);
        msgpack_pack_str(&tmp_pck, 12);
        msgpack_pack_str_body(&tmp_pck, "global_count", 12);
        msgpack_pack_int(&tmp_pck, global_count);
    }

    msgpack_unpacked_destroy(&result);

    *out_buf = tmp_sbuf.data;
    *out_bytes = tmp_sbuf.size;

    return FLB_FILTER_MODIFIED;
}

static int cb_init(struct flb_filter_instance *f_ins,
                   struct flb_config *config,
                   void *data)
{
    flb_plg_info(f_ins, "level counter plugin initialized");
    return 0;
}

static int cb_exit(void *data, struct flb_config *config)
{
    return 0;
}

struct flb_filter_plugin filter_level_counter_plugin = {
    .name         = "level_counter",
    .description  = "Counts logs per level",
    .cb_init      = cb_init,
    .cb_filter    = cb_filter,
    .cb_exit      = cb_exit,
    .flags        = 0
};

        
