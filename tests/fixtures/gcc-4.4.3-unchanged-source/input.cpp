int leaf(int value) { return value + 3; }

int middle(int value)
{
    for (int index = 0; index < 12; ++index)
        value = leaf(value) * (value > index ? 7 : 11);
    return value;
}

int caller(int value) { return middle(value) - 1; }
